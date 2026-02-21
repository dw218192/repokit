"""Tests for Claude backend — build_command() and plugin generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_tools.agent.claude import Claude


class TestBuildCommand:
    def test_basic_command(self):
        """build_command() always pre-approves the safe read/edit tools."""
        claude = Claude()
        cmd = claude.build_command()
        assert cmd[0] == "claude"
        assert "--allowedTools" in cmd
        for tool in ("Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch"):
            assert tool in cmd

    def test_no_bash_by_default(self):
        claude = Claude()
        cmd = claude.build_command()
        assert "Bash" not in cmd

    def test_role_adds_bash(self):
        claude = Claude()
        cmd = claude.build_command(role="worker")
        assert "Bash" in cmd

    def test_role_prompt_appended(self):
        claude = Claude()
        cmd = claude.build_command(role_prompt="You are a test worker.")
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert "You are a test worker." in cmd[idx + 1]

    def test_hook_settings_wired(self, tmp_path):
        """When rules_path and project_root are given, plugin hooks are written."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        cmd = claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        hooks_path = tmp_path / "_agent" / "plugin" / "hooks" / "hooks.json"
        assert hooks_path.exists(), "hooks file must be written to disk"
        assert "--plugin-dir" in cmd

        data = json.loads(hooks_path.read_text())
        pre = data["hooks"]["PreToolUse"]
        assert len(pre) == 1
        assert pre[0]["matcher"] == "Bash"
        hook_cmd = pre[0]["hooks"][0]["command"]
        assert "check_bash" in hook_cmd
        assert "\\" not in hook_cmd

    def test_no_settings_without_rules(self):
        """Without rules_path, no plugin dir is written."""
        claude = Claude()
        cmd = claude.build_command()
        assert "--plugin-dir" not in cmd

    def test_role_forwarded_to_hook_command(self, tmp_path):
        """When role is provided, --role is included in the hook command."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            role="worker",
            rules_path=rules,
            project_root=tmp_path,
        )
        hooks_path = tmp_path / "_agent" / "plugin" / "hooks" / "hooks.json"
        data = json.loads(hooks_path.read_text())
        hook_cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert "--role" in hook_cmd
        assert "worker" in hook_cmd

    def test_no_role_no_role_flag_in_hook(self, tmp_path):
        """When no role is given, --role is not added to the hook command."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        hooks_path = tmp_path / "_agent" / "plugin" / "hooks" / "hooks.json"
        data = json.loads(hooks_path.read_text())
        hook_cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert "--role" not in hook_cmd

    def test_mcp_port_adds_stop_hook_and_mcp_server_for_worker(self, tmp_path):
        """Worker with mcp_port gets Stop hook and MCP server config."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        claude = Claude()
        claude.build_command(
            role="worker",
            rules_path=rules,
            project_root=tmp_path,
            mcp_port=18042,
            cwd=cwd,
        )
        data = json.loads((cwd / "_agent" / "plugin" / "hooks" / "hooks.json").read_text())
        mcp = json.loads((cwd / "_agent" / "plugin" / ".mcp.json").read_text())

        # Stop hook present
        assert "Stop" in data["hooks"]
        stop_cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert "stop_hook" in stop_cmd
        assert "18042" in stop_cmd

        # MCP server config present
        assert "mcpServers" in mcp
        assert "team" in mcp["mcpServers"]
        assert "18042" in mcp["mcpServers"]["team"]["url"]

    def test_mcp_port_adds_stop_hook_for_reviewer(self, tmp_path):
        """Reviewer with mcp_port also gets Stop hook and MCP server config."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            role="reviewer",
            rules_path=rules,
            project_root=tmp_path,
            mcp_port=18043,
        )
        data = json.loads((tmp_path / "_agent" / "plugin" / "hooks" / "hooks.json").read_text())
        mcp = json.loads((tmp_path / "_agent" / "plugin" / ".mcp.json").read_text())
        assert "Stop" in data["hooks"]
        assert "mcpServers" in mcp

    def test_orchestrator_gets_no_stop_hook(self, tmp_path):
        """Orchestrator is not subject to idle kill — no Stop hook or MCP server."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            role="orchestrator",
            rules_path=rules,
            project_root=tmp_path,
            mcp_port=18042,
        )
        data = json.loads((tmp_path / "_agent" / "plugin" / "hooks" / "hooks.json").read_text())
        assert "Stop" not in data["hooks"]
        assert not (tmp_path / "_agent" / "plugin" / ".mcp.json").exists()

    def test_cwd_plugin_path(self, tmp_path):
        """When cwd is given, plugin is written inside {cwd}/_agent/plugin/."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")
        cwd = tmp_path / "worktree"
        cwd.mkdir()

        claude = Claude()
        claude.build_command(
            role="worker",
            rules_path=rules,
            project_root=tmp_path,
            cwd=cwd,
        )
        assert (cwd / "_agent" / "plugin" / "hooks" / "hooks.json").exists()
        # Not written to project_root
        assert not (tmp_path / "_agent" / "plugin" / "hooks" / "hooks.json").exists()

    def test_solo_mode_has_coderabbit_stdio_mcp(self, tmp_path):
        """Solo mode (mcp_port=None) gets a 'coderabbit' stdio MCP entry."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
            mcp_port=None,
        )
        mcp = json.loads((tmp_path / "_agent" / "plugin" / ".mcp.json").read_text())

        assert "mcpServers" in mcp
        assert "coderabbit" in mcp["mcpServers"]
        entry = mcp["mcpServers"]["coderabbit"]
        assert entry["type"] == "stdio"
        assert "coderabbit_mcp" in " ".join(entry["args"])

    def test_team_mode_no_coderabbit_stdio(self, tmp_path):
        """Team worker (mcp_port set) uses the HTTP 'team' MCP, not the stdio 'coderabbit' one."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            role="worker",
            rules_path=rules,
            project_root=tmp_path,
            mcp_port=18042,
        )
        mcp = json.loads((tmp_path / "_agent" / "plugin" / ".mcp.json").read_text())

        assert "mcpServers" in mcp
        assert "team" in mcp["mcpServers"]
        assert "coderabbit" not in mcp["mcpServers"]

    def test_plugin_dir_in_command(self, tmp_path):
        """--plugin-dir appears in command and points to the plugin directory."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        cmd = claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        assert "--plugin-dir" in cmd
        idx = cmd.index("--plugin-dir")
        plugin_path = Path(cmd[idx + 1])
        assert plugin_path == tmp_path / "_agent" / "plugin"

    def test_plugin_manifest_written(self, tmp_path):
        """Plugin manifest .claude-plugin/plugin.json is written."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        manifest = json.loads(
            (tmp_path / "_agent" / "plugin" / ".claude-plugin" / "plugin.json").read_text()
        )
        assert manifest["name"] == "repokit-agent"

    def test_no_settings_local_json_written(self, tmp_path):
        """.claude/settings.local.json must NOT be written."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        assert not (tmp_path / ".claude" / "settings.local.json").exists()

    def test_stale_mcp_json_removed(self, tmp_path):
        """When MCP config is not needed, a stale .mcp.json from a previous run is removed."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        plugin_dir = tmp_path / "_agent" / "plugin"
        plugin_dir.mkdir(parents=True)
        stale = plugin_dir / ".mcp.json"
        stale.write_text('{"old": true}', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            role="orchestrator",
            rules_path=rules,
            project_root=tmp_path,
            mcp_port=18042,
        )
        assert not stale.exists(), "stale .mcp.json should be removed"

    def test_empty_path_with_none_raises(self):
        """Path('') with None should raise ValueError (Issue 7: bool(Path('')) is True)."""
        claude = Claude()
        with pytest.raises(ValueError, match="must both be provided together"):
            claude.build_command(rules_path=Path(""), project_root=None)
