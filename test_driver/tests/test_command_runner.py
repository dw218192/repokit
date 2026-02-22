"""Tests for CommandRunnerTool base class (repo_tools.command_runner)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from repo_tools.command_runner import CommandRunnerTool


class TestCommandRunnerTool:
    """Unit tests for CommandRunnerTool.execute()."""

    def test_token_expansion(self, make_tool_context):
        """Token placeholders in the command string are resolved before execution."""
        ctx = make_tool_context(
            dimensions={"platform": "linux-x64", "build_type": "Debug"},
        )
        tool = CommandRunnerTool()
        args = {"command": "echo {build_type}"}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            mock_run.assert_called_once()
            resolved_cmd = mock_run.call_args[0][0]
            assert resolved_cmd == ["echo", "Debug"]

    def test_missing_command_exits(self, make_tool_context):
        """Omitting 'command' from args raises SystemExit(1)."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        args = {}

        with pytest.raises(SystemExit) as exc_info:
            tool.execute(ctx, args)
        assert exc_info.value.code == 1

    def test_args_merged_into_tokens(self, make_tool_context):
        """Extra keys in the args dict are available for token expansion."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        args = {"command": "deploy --env {target_env}", "target_env": "staging"}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            mock_run.assert_called_once()
            resolved_cmd = mock_run.call_args[0][0]
            assert resolved_cmd == ["deploy", "--env", "staging"]

    def test_dry_run_skips_execution(self, make_tool_context):
        """dry_run=True logs the resolved command without calling run_command."""
        ctx = make_tool_context(dimensions={"platform": "linux-x64", "build_type": "Debug"})
        tool = CommandRunnerTool()
        args = {"command": "cmake --build . --config {build_type}", "dry_run": True}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            mock_run.assert_not_called()

    def test_dimension_tokens_from_context(self, make_tool_context):
        """Dimension tokens set at group level (build_type, platform) flow through to command."""
        ctx = make_tool_context(dimensions={"platform": "linux-x64", "build_type": "Release"})
        tool = CommandRunnerTool()
        args = {"command": "cmake --config {build_type} --target {platform}"}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            resolved_cmd = mock_run.call_args[0][0]
            assert "Release" in resolved_cmd
            assert any("Release" in part for part in resolved_cmd)


class TestCommandRunnerListCommands:
    """Tests for list-of-commands support in CommandRunnerTool."""

    def test_list_commands_run_via_command_group(self, make_tool_context):
        """A list command value runs each step through a CommandGroup."""
        ctx = make_tool_context(dimensions={"build_type": "Release"})
        tool = CommandRunnerTool()
        tool.name = "build"
        args = {"command": ["cmake --build .", "cmake --install ."]}

        with patch("repo_tools.command_runner.CommandGroup") as MockGroup:
            mock_group = MagicMock()
            MockGroup.return_value.__enter__ = MagicMock(return_value=mock_group)
            MockGroup.return_value.__exit__ = MagicMock(return_value=False)
            tool.execute(ctx, args)
            assert mock_group.run.call_count == 2
            first_cmd = mock_group.run.call_args_list[0][0][0]
            assert first_cmd == ["cmake", "--build", "."]

    def test_list_commands_token_expansion(self, make_tool_context):
        """Tokens are expanded in each list step."""
        ctx = make_tool_context(dimensions={"build_type": "Release"})
        tool = CommandRunnerTool()
        tool.name = "build"
        args = {"command": ["echo {build_type}", "echo done"]}

        with patch("repo_tools.command_runner.CommandGroup") as MockGroup:
            mock_group = MagicMock()
            MockGroup.return_value.__enter__ = MagicMock(return_value=mock_group)
            MockGroup.return_value.__exit__ = MagicMock(return_value=False)
            tool.execute(ctx, args)
            first_cmd = mock_group.run.call_args_list[0][0][0]
            assert first_cmd == ["echo", "Release"]

    def test_list_dry_run_prints_each_step(self, make_tool_context, capture_logs):
        """dry_run with a list command prints each resolved step."""
        ctx = make_tool_context(dimensions={"build_type": "Debug"})
        tool = CommandRunnerTool()
        tool.name = "build"
        args = {
            "command": ["cmake --build .", "cmake --install ."],
            "dry_run": True,
        }

        with patch("repo_tools.command_runner.CommandGroup") as MockGroup:
            tool.execute(ctx, args)
            MockGroup.assert_not_called()
        output = capture_logs.getvalue()
        assert "[1/2]" in output
        assert "[2/2]" in output

    def test_env_script_and_cwd_passed_to_group(self, make_tool_context, tmp_path):
        """env_script and cwd are resolved and forwarded to CommandGroup."""
        ctx = make_tool_context(dimensions={"build_type": "Debug"})
        tool = CommandRunnerTool()
        tool.name = "build"
        args = {
            "command": ["echo hello"],
            "env_script": str(tmp_path / "setup"),
            "cwd": str(tmp_path),
        }

        with patch("repo_tools.command_runner.CommandGroup") as MockGroup:
            mock_group = MagicMock()
            MockGroup.return_value.__enter__ = MagicMock(return_value=mock_group)
            MockGroup.return_value.__exit__ = MagicMock(return_value=False)
            tool.execute(ctx, args)
            call_kwargs = MockGroup.call_args
            assert call_kwargs[1]["env_script"] == tmp_path / "setup"
            assert call_kwargs[1]["cwd"] == tmp_path


class TestCommandRunnerEnvScriptCwd:
    """Tests for env_script and cwd with string commands."""

    def test_env_script_passed_to_run_command(self, make_tool_context, tmp_path):
        """env_script is resolved and passed to run_command for string commands."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        args = {"command": "echo hello", "env_script": str(tmp_path / "env")}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            assert mock_run.call_args[1]["env_script"] == tmp_path / "env"

    def test_cwd_passed_to_run_command(self, make_tool_context, tmp_path):
        """cwd is resolved and passed to run_command for string commands."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        args = {"command": "echo hello", "cwd": str(tmp_path)}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            assert mock_run.call_args[1]["cwd"] == tmp_path

    def test_env_script_and_cwd_not_leaked_as_tokens(self, make_tool_context, tmp_path):
        """env_script and cwd should not be available as token expansions."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        args = {
            "command": "echo hello",
            "env_script": "/some/path",
            "cwd": "/some/dir",
            "custom_var": "visible",
        }

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            # custom_var should be merged into tokens, but env_script/cwd should not
            resolved_cmd = mock_run.call_args[0][0]
            assert resolved_cmd == ["echo", "hello"]

    def test_env_script_token_expansion(self, make_tool_context, tmp_path):
        """env_script value can use token placeholders."""
        ctx = make_tool_context(
            tokens_override={"tools_dir": str(tmp_path)},
        )
        tool = CommandRunnerTool()
        args = {"command": "echo ok", "env_script": "{tools_dir}/setup"}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            assert mock_run.call_args[1]["env_script"] == tmp_path / "setup"
