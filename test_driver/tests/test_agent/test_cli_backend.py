"""Tests for CLI subprocess backend."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_tools.agent.claude._base import ClaudeBackend
from repo_tools.agent.claude._cli import CliBackend, _find_claude_cli, _write_plugin
from repo_tools.agent.claude._cli import PLUGIN_MANIFEST
from repo_tools.agent.claude._shared import ALLOWED_TOOLS, OUTPUT_SCHEMAS


# ── Protocol conformance ─────────────────────────────────────────


class TestProtocol:
    def test_satisfies_protocol(self):
        """CliBackend satisfies the ClaudeBackend protocol."""
        assert isinstance(CliBackend(), ClaudeBackend)


# ── _build_command tests ─────────────────────────────────────────


class TestBuildCommand:
    def test_base_allowed_tools(self, tmp_path):
        """Base tools are always in the command."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        cmd = CliBackend._build_command(
            rules_path=rules, project_root=tmp_path,
        )
        for tool_name in ALLOWED_TOOLS:
            assert tool_name in cmd

    def test_no_bash_without_role(self, tmp_path):
        """Without a role, Bash is NOT in the command."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        cmd = CliBackend._build_command(
            rules_path=rules, project_root=tmp_path,
        )
        # Bash should not appear after --allowedTools
        idx = cmd.index("--allowedTools")
        # Find the next flag (starts with --)
        tools_end = len(cmd)
        for i in range(idx + 1, len(cmd)):
            if cmd[i].startswith("--"):
                tools_end = i
                break
        tool_args = cmd[idx + 1:tools_end]
        assert "Bash" not in tool_args

    def test_role_adds_bash(self, tmp_path):
        """With a role, Bash IS in the command."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        cmd = CliBackend._build_command(
            role="worker", rules_path=rules, project_root=tmp_path,
        )
        assert "Bash" in cmd

    def test_headless_has_prompt_and_json(self, tmp_path):
        """Headless mode adds -p, --output-format json, --no-session-persistence."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        cmd = CliBackend._build_command(
            prompt="Do something", role="worker",
            rules_path=rules, project_root=tmp_path,
        )
        assert "-p" in cmd
        assert "Do something" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd
        assert "--no-session-persistence" in cmd

    def test_interactive_has_no_prompt(self, tmp_path):
        """Interactive mode (no prompt) has no -p flag."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        cmd = CliBackend._build_command(
            rules_path=rules, project_root=tmp_path,
        )
        assert "-p" not in cmd

    def test_role_prompt_appended(self, tmp_path):
        """role_prompt is passed via --append-system-prompt."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        cmd = CliBackend._build_command(
            role="worker", role_prompt="You are a worker.",
            rules_path=rules, project_root=tmp_path,
        )
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "You are a worker."

    def test_max_turns_forwarded(self, tmp_path):
        """max_turns from tool_config is added to command."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        cmd = CliBackend._build_command(
            prompt="go", role="worker",
            rules_path=rules, project_root=tmp_path,
            tool_config={"max_turns": 25},
        )
        assert "--max-turns" in cmd
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "25"

    def test_json_schema_for_worker(self, tmp_path):
        """Worker role gets --json-schema with the worker output schema."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        cmd = CliBackend._build_command(
            prompt="go", role="worker",
            rules_path=rules, project_root=tmp_path,
        )
        assert "--json-schema" in cmd
        idx = cmd.index("--json-schema")
        schema = json.loads(cmd[idx + 1])
        assert "ticket_id" in schema["properties"]

    def test_json_schema_for_reviewer(self, tmp_path):
        """Reviewer role gets --json-schema with the reviewer output schema."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        cmd = CliBackend._build_command(
            prompt="go", role="reviewer",
            rules_path=rules, project_root=tmp_path,
        )
        assert "--json-schema" in cmd
        idx = cmd.index("--json-schema")
        schema = json.loads(cmd[idx + 1])
        assert "result" in schema["properties"]
        assert "criteria" in schema["properties"]

    def test_plugin_dir_in_command(self, tmp_path):
        """--plugin-dir is added when rules_path and project_root are set."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        cmd = CliBackend._build_command(
            role="worker", rules_path=rules, project_root=tmp_path,
        )
        assert "--plugin-dir" in cmd

    def test_requires_both_rules_and_root(self, tmp_path):
        """Raises ValueError if only one of rules_path/project_root is given."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        with pytest.raises(ValueError, match="must both be provided"):
            CliBackend._build_command(rules_path=rules)

    def test_debug_hooks(self, tmp_path):
        """debug_hooks in tool_config adds -d hooks."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        cmd = CliBackend._build_command(
            rules_path=rules, project_root=tmp_path,
            tool_config={"debug_hooks": True},
        )
        assert "-d" in cmd
        idx = cmd.index("-d")
        assert cmd[idx + 1] == "hooks"


# ── _write_plugin tests ─────────────────────────────────────────


class TestWritePlugin:
    def test_creates_manifest(self, tmp_path):
        """Creates .claude-plugin/plugin.json with correct manifest."""
        plugin_dir = tmp_path / "plugin"
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        _write_plugin(plugin_dir, rules, tmp_path)

        manifest = json.loads(
            (plugin_dir / ".claude-plugin" / "plugin.json").read_text()
        )
        assert manifest == PLUGIN_MANIFEST

    def test_creates_hooks_json(self, tmp_path):
        """Creates hooks/hooks.json with PreToolUse and PermissionRequest."""
        plugin_dir = tmp_path / "plugin"
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        _write_plugin(plugin_dir, rules, tmp_path)

        hooks = json.loads(
            (plugin_dir / "hooks" / "hooks.json").read_text()
        )
        assert "PreToolUse" in hooks["hooks"]
        assert "PermissionRequest" in hooks["hooks"]

    def test_creates_mcp_json(self, tmp_path):
        """Creates .mcp.json with coderabbit, lint, and tickets servers."""
        plugin_dir = tmp_path / "plugin"
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        _write_plugin(plugin_dir, rules, tmp_path)

        mcp = json.loads((plugin_dir / ".mcp.json").read_text())
        servers = mcp["mcpServers"]
        assert "coderabbit" in servers
        assert "lint" in servers
        assert "tickets" in servers

    def test_role_in_ticket_args(self, tmp_path):
        """Role is passed to ticket MCP server args."""
        plugin_dir = tmp_path / "plugin"
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        _write_plugin(plugin_dir, rules, tmp_path, role="worker")

        mcp = json.loads((plugin_dir / ".mcp.json").read_text())
        ticket_args = mcp["mcpServers"]["tickets"]["args"]
        assert "--role" in ticket_args
        assert "worker" in ticket_args

    def test_ruff_config_in_lint_args(self, tmp_path):
        """Ruff select/ignore are passed to lint MCP server args."""
        plugin_dir = tmp_path / "plugin"
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        _write_plugin(
            plugin_dir, rules, tmp_path,
            tool_config={"ruff_select": "E,F", "ruff_ignore": "E501"},
        )

        mcp = json.loads((plugin_dir / ".mcp.json").read_text())
        lint_args = mcp["mcpServers"]["lint"]["args"]
        assert "--select" in lint_args
        assert "E,F" in lint_args
        assert "--ignore" in lint_args
        assert "E501" in lint_args

    def test_role_in_check_bash_hook(self, tmp_path):
        """Role is passed in check_bash hook command."""
        plugin_dir = tmp_path / "plugin"
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        _write_plugin(plugin_dir, rules, tmp_path, role="reviewer")

        hooks = json.loads(
            (plugin_dir / "hooks" / "hooks.json").read_text()
        )
        bash_hook = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert "--role" in bash_hook
        assert "reviewer" in bash_hook

    def test_human_ticket_review_adds_hook(self, tmp_path):
        """human_ticket_review adds a PreToolUse hook for create_ticket."""
        plugin_dir = tmp_path / "plugin"
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        _write_plugin(
            plugin_dir, rules, tmp_path,
            tool_config={"agent": {
                "human_ticket_review": True,
                "required_criteria": ["Must pass tests", "Must lint clean"],
            }},
        )

        hooks = json.loads(
            (plugin_dir / "hooks" / "hooks.json").read_text()
        )
        pre_tool = hooks["hooks"]["PreToolUse"]
        # Should have Bash hook + create_ticket hook
        assert len(pre_tool) == 2
        ticket_hook = pre_tool[1]
        assert ticket_hook["matcher"] == "create_ticket$"
        cmd = ticket_hook["hooks"][0]["command"]
        assert "approve_ticket" in cmd
        assert "Must pass tests" in cmd
        assert "Must lint clean" in cmd

    def test_no_ticket_review_hook_by_default(self, tmp_path):
        """Without human_ticket_review, no create_ticket hook is added."""
        plugin_dir = tmp_path / "plugin"
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        _write_plugin(plugin_dir, rules, tmp_path)

        hooks = json.loads(
            (plugin_dir / "hooks" / "hooks.json").read_text()
        )
        pre_tool = hooks["hooks"]["PreToolUse"]
        assert len(pre_tool) == 1  # Only the Bash hook

    def test_dispatch_in_orchestrator_mcp_config(self, tmp_path):
        """Dispatch server is included for orchestrator role."""
        plugin_dir = tmp_path / "plugin"
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        _write_plugin(plugin_dir, rules, tmp_path, role="orchestrator")

        mcp = json.loads((plugin_dir / ".mcp.json").read_text())
        assert "dispatch" in mcp["mcpServers"]
        dispatch_args = mcp["mcpServers"]["dispatch"]["args"]
        assert "-m" in dispatch_args
        assert "repo_tools.agent.mcp.dispatch" in dispatch_args

    def test_dispatch_not_in_worker_mcp_config(self, tmp_path):
        """Dispatch server is NOT included for worker role."""
        plugin_dir = tmp_path / "plugin"
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        _write_plugin(plugin_dir, rules, tmp_path, role="worker")

        mcp = json.loads((plugin_dir / ".mcp.json").read_text())
        assert "dispatch" not in mcp["mcpServers"]

    def test_registered_tools_in_mcp_config(self, tmp_path):
        """Registered RepoTool subclasses appear in .mcp.json repo_cmd server."""
        plugin_dir = tmp_path / "plugin"
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        fake_registered = [
            {"name": "clean", "description": "Clean up"},
            {"name": "format", "description": "Format code"},
        ]
        with patch(
            "repo_tools.agent.repo_cmd._discover_registered_tools",
            return_value=fake_registered,
        ):
            _write_plugin(plugin_dir, rules, tmp_path)

        mcp = json.loads((plugin_dir / ".mcp.json").read_text())
        assert "repo_cmd" in mcp["mcpServers"]
        repo_args = mcp["mcpServers"]["repo_cmd"]["args"]
        assert "--extra-tools" in repo_args
        extra_idx = repo_args.index("--extra-tools")
        extra_tools = json.loads(repo_args[extra_idx + 1])
        names = {t["name"] for t in extra_tools}
        assert "clean" in names
        assert "format" in names


# ── run_headless / run_interactive tests ─────────────────────────


class TestRunHeadless:
    @patch("repo_tools.agent.claude._cli.subprocess.run")
    def test_returns_stdout_and_returncode(self, mock_run, tmp_path):
        """run_headless returns (stdout, returncode) from subprocess."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        mock_run.return_value = MagicMock(
            stdout='{"type": "result"}', returncode=0,
        )

        backend = CliBackend()
        stdout, rc = backend.run_headless(
            prompt="Do work",
            role="worker",
            rules_path=rules,
            project_root=tmp_path,
            cwd=tmp_path,
        )
        assert stdout == '{"type": "result"}'
        assert rc == 0
        mock_run.assert_called_once()

    @patch("repo_tools.agent.claude._cli.subprocess.run")
    def test_passes_cwd(self, mock_run, tmp_path):
        """run_headless passes cwd to subprocess."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        mock_run.return_value = MagicMock(stdout="", returncode=0)

        backend = CliBackend()
        backend.run_headless(
            prompt="go", role="worker",
            rules_path=rules, project_root=tmp_path,
            cwd=tmp_path / "sub",
        )
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["cwd"] == str(tmp_path / "sub")

    @patch("repo_tools.agent.claude._cli.subprocess.run")
    def test_captures_output(self, mock_run, tmp_path):
        """run_headless uses capture_output=True."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        mock_run.return_value = MagicMock(stdout="", returncode=0)

        backend = CliBackend()
        backend.run_headless(
            prompt="go", role="worker",
            rules_path=rules, project_root=tmp_path,
        )
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["capture_output"] is True
        assert call_kwargs.kwargs["text"] is True


class TestRunInteractive:
    @patch("repo_tools.agent.claude._cli.subprocess.run")
    def test_returns_exit_code(self, mock_run, tmp_path):
        """run_interactive returns the subprocess exit code."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        mock_run.return_value = MagicMock(returncode=0)

        backend = CliBackend()
        rc, session_id = backend.run_interactive(
            rules_path=rules, project_root=tmp_path, cwd=tmp_path,
        )
        assert rc == 0

    @patch("repo_tools.agent.claude._cli.subprocess.run")
    def test_no_capture_output(self, mock_run, tmp_path):
        """run_interactive does NOT capture output (user sees it)."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        mock_run.return_value = MagicMock(returncode=0)

        backend = CliBackend()
        backend.run_interactive(
            rules_path=rules, project_root=tmp_path, cwd=tmp_path,
        )
        call_kwargs = mock_run.call_args
        assert "capture_output" not in call_kwargs.kwargs or not call_kwargs.kwargs.get("capture_output")

    @patch("repo_tools.agent.claude._cli.subprocess.run")
    def test_passes_cwd(self, mock_run, tmp_path):
        """run_interactive passes cwd to subprocess."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        mock_run.return_value = MagicMock(returncode=0)

        backend = CliBackend()
        backend.run_interactive(
            rules_path=rules, project_root=tmp_path,
            cwd=tmp_path / "workdir",
        )
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["cwd"] == str(tmp_path / "workdir")


# ── _find_claude_cli tests ───────────────────────────────────────


class TestFindClaudeCli:
    @patch("repo_tools.agent.claude._cli.sys")
    def test_non_windows_returns_claude(self, mock_sys):
        """On non-Windows platforms, returns plain 'claude'."""
        mock_sys.platform = "linux"
        assert _find_claude_cli() == "claude"

    @patch("repo_tools.agent.claude._cli.shutil.which", return_value=None)
    @patch("repo_tools.agent.claude._cli.sys")
    def test_windows_no_which_returns_claude(self, mock_sys, mock_which):
        """On Windows when shutil.which returns None, returns plain 'claude'."""
        mock_sys.platform = "win32"
        assert _find_claude_cli() == "claude"

    @patch("repo_tools.agent.claude._cli.sys")
    def test_windows_ps1_prefers_cmd(self, mock_sys, tmp_path):
        """On Windows, .ps1 result is swapped for .cmd when it exists."""
        mock_sys.platform = "win32"
        ps1 = tmp_path / "claude.ps1"
        cmd = tmp_path / "claude.cmd"
        ps1.write_text("# ps1 stub", encoding="utf-8")
        cmd.write_text("@rem cmd stub", encoding="utf-8")

        with patch(
            "repo_tools.agent.claude._cli.shutil.which",
            return_value=str(ps1),
        ):
            result = _find_claude_cli()
        assert result == str(cmd)

    @patch("repo_tools.agent.claude._cli.shutil.which", return_value=r"C:\bin\claude.exe")
    @patch("repo_tools.agent.claude._cli.sys")
    def test_windows_exe_returned_as_is(self, mock_sys, mock_which):
        """On Windows, a .exe path is returned unchanged."""
        mock_sys.platform = "win32"
        assert _find_claude_cli() == r"C:\bin\claude.exe"
