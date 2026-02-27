"""Tests for CommandRunnerTool (repo_tools.command_runner)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from repo_tools.command_runner import CommandRunnerTool, _validate_steps, _parse_env_list


# ── _validate_steps ──────────────────────────────────────────────


class TestValidateSteps:
    def test_string_normalized(self):
        result = _validate_steps("build", ["echo hello"])
        assert result == [{"command": "echo hello"}]

    def test_dict_form(self):
        raw = [{"command": "echo", "cwd": "/tmp", "env_script": "setup.sh", "env": ["K=V"]}]
        result = _validate_steps("build", raw)
        assert result == raw

    def test_missing_command_key(self):
        with pytest.raises(SystemExit):
            _validate_steps("build", [{"cwd": "/tmp"}])

    def test_unknown_keys(self):
        with pytest.raises(SystemExit):
            _validate_steps("build", [{"command": "echo", "bogus": "val"}])

    def test_bad_env_type_not_list(self):
        with pytest.raises(SystemExit):
            _validate_steps("build", [{"command": "echo", "env": "FOO=BAR"}])

    def test_bad_env_type_non_str_items(self):
        with pytest.raises(SystemExit):
            _validate_steps("build", [{"command": "echo", "env": [123]}])

    def test_non_list_input(self):
        with pytest.raises(SystemExit):
            _validate_steps("build", "not a list")

    def test_non_str_dict_item(self):
        with pytest.raises(SystemExit):
            _validate_steps("build", [42])

    def test_multiple_steps(self):
        result = _validate_steps("build", ["step1", {"command": "step2"}])
        assert len(result) == 2
        assert result[0] == {"command": "step1"}
        assert result[1] == {"command": "step2"}


# ── _parse_env_list ──────────────────────────────────────────────


class TestParseEnvList:
    def test_valid_entries(self):
        result = _parse_env_list(["FOO=bar", "BAZ=qux=extra"])
        assert result == {"FOO": "bar", "BAZ": "qux=extra"}

    def test_missing_equals(self):
        with pytest.raises(SystemExit):
            _parse_env_list(["NO_EQUALS"])

    def test_empty_value(self):
        result = _parse_env_list(["KEY="])
        assert result == {"KEY": ""}


# ── CommandRunnerTool.execute ────────────────────────────────────


class TestCommandRunnerExecute:
    def test_token_expansion(self, make_tool_context):
        """Token placeholders in the step command are resolved."""
        ctx = make_tool_context(dimensions={"platform": "linux-x64", "build_type": "Debug"})
        tool = CommandRunnerTool()
        tool.name = "test"
        args = {"steps": ["echo {build_type}"]}

        with patch("repo_tools.command_runner.ShellCommand") as MockSC:
            tool.execute(ctx, args)
            MockSC.assert_called_once()
            assert MockSC.call_args[0][0] == ["echo", "Debug"]
            MockSC.return_value.exec.assert_called_once()

    def test_missing_steps_exits(self, make_tool_context):
        """Omitting 'steps' from args raises SystemExit(1)."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        tool.name = "test"
        args = {}

        with pytest.raises(SystemExit) as exc_info:
            tool.execute(ctx, args)
        assert exc_info.value.code == 1

    def test_args_merged_into_tokens(self, make_tool_context):
        """Extra keys in the args dict are available for token expansion."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        tool.name = "test"
        args = {"steps": ["deploy --env {target_env}"], "target_env": "staging"}

        with patch("repo_tools.command_runner.ShellCommand") as MockSC:
            tool.execute(ctx, args)
            assert MockSC.call_args[0][0] == ["deploy", "--env", "staging"]

    def test_dry_run_skips_execution(self, make_tool_context):
        """dry_run=True logs the resolved command without executing."""
        ctx = make_tool_context(dimensions={"platform": "linux-x64", "build_type": "Debug"})
        tool = CommandRunnerTool()
        tool.name = "test"
        args = {"steps": ["cmake --build . --config {build_type}"], "dry_run": True}

        with patch("repo_tools.command_runner.ShellCommand") as MockSC:
            tool.execute(ctx, args)
            MockSC.assert_not_called()


class TestSingleVsMultipleSteps:
    def test_single_step_uses_shell_command(self, make_tool_context):
        """A single step calls ShellCommand.exec() directly (no CommandGroup)."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        tool.name = "test"
        args = {"steps": ["echo hello"]}

        with patch("repo_tools.command_runner.ShellCommand") as MockSC, \
             patch("repo_tools.command_runner.CommandGroup") as MockGroup:
            tool.execute(ctx, args)
            MockSC.assert_called_once()
            MockSC.return_value.exec.assert_called_once()
            MockGroup.assert_not_called()

    def test_multiple_steps_use_command_group(self, make_tool_context):
        """Multiple steps use a CommandGroup."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        tool.name = "build"
        args = {"steps": ["cmake --build .", "cmake --install ."]}

        with patch("repo_tools.command_runner.CommandGroup") as MockGroup:
            mock_group = MagicMock()
            MockGroup.return_value.__enter__ = MagicMock(return_value=mock_group)
            MockGroup.return_value.__exit__ = MagicMock(return_value=False)
            tool.execute(ctx, args)
            assert mock_group.run.call_count == 2
            assert mock_group.run.call_args_list[0][0][0] == ["cmake", "--build", "."]

    def test_multi_step_dry_run(self, make_tool_context, capture_logs):
        """dry_run with multiple steps prints each resolved step."""
        ctx = make_tool_context(dimensions={"build_type": "Debug"})
        tool = CommandRunnerTool()
        tool.name = "build"
        args = {"steps": ["cmake --build .", "cmake --install ."], "dry_run": True}

        with patch("repo_tools.command_runner.CommandGroup") as MockGroup:
            tool.execute(ctx, args)
            MockGroup.assert_not_called()
        output = capture_logs.getvalue()
        assert "[1/2]" in output
        assert "[2/2]" in output


class TestStepOverrides:
    def test_env_in_step_passed_to_shell_command(self, make_tool_context):
        """env list in a step dict is parsed and passed to ShellCommand."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        tool.name = "test"
        args = {"steps": [{"command": "echo hi", "env": ["FOO=bar"]}]}

        with patch("repo_tools.command_runner.ShellCommand") as MockSC:
            tool.execute(ctx, args)
            assert MockSC.call_args[1]["env"] == {"FOO": "bar"}

    def test_cwd_in_step_passed_to_shell_command(self, make_tool_context, tmp_path):
        """cwd in a step dict is resolved and passed to ShellCommand."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        tool.name = "test"
        args = {"steps": [{"command": "echo hi", "cwd": str(tmp_path)}]}

        with patch("repo_tools.command_runner.ShellCommand") as MockSC:
            tool.execute(ctx, args)
            assert MockSC.call_args[1]["cwd"] == tmp_path

    def test_env_script_in_step_passed_to_shell_command(self, make_tool_context, tmp_path):
        """env_script in a step dict is resolved and passed to ShellCommand."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        tool.name = "test"
        args = {"steps": [{"command": "echo hi", "env_script": str(tmp_path / "setup")}]}

        with patch("repo_tools.command_runner.ShellCommand") as MockSC:
            tool.execute(ctx, args)
            assert MockSC.call_args[1]["env_script"] == tmp_path / "setup"

    def test_env_token_expansion(self, make_tool_context):
        """Token placeholders in env values are expanded."""
        ctx = make_tool_context(
            tokens_override={"my_dir": "/opt/tools"},
            dimensions={"platform": "linux-x64", "build_type": "Debug"},
        )
        tool = CommandRunnerTool()
        tool.name = "test"
        args = {"steps": [{"command": "echo hi", "env": ["PATH={my_dir}/bin"]}]}

        with patch("repo_tools.command_runner.ShellCommand") as MockSC:
            tool.execute(ctx, args)
            assert MockSC.call_args[1]["env"] == {"PATH": "/opt/tools/bin"}

    def test_env_flows_to_command_group(self, make_tool_context):
        """env in multi-step is forwarded through CommandGroup.run."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        tool.name = "build"
        args = {
            "steps": [
                {"command": "step1", "env": ["A=1"]},
                {"command": "step2", "env": ["B=2"]},
            ]
        }

        with patch("repo_tools.command_runner.CommandGroup") as MockGroup:
            mock_group = MagicMock()
            MockGroup.return_value.__enter__ = MagicMock(return_value=mock_group)
            MockGroup.return_value.__exit__ = MagicMock(return_value=False)
            tool.execute(ctx, args)
            calls = mock_group.run.call_args_list
            assert calls[0][1]["env"] == {"A": "1"}
            assert calls[1][1]["env"] == {"B": "2"}
