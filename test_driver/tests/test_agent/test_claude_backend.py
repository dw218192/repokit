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
        assert "repo_tools.agent.hooks" in hook_cmd
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
        hooks_path = tmp_path / "_agent" / "plugin-worker" / "hooks" / "hooks.json"
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

    def test_no_stop_hook(self, tmp_path):
        """No Stop hook is generated (idle tracking removed)."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            role="worker",
            rules_path=rules,
            project_root=tmp_path,
        )
        data = json.loads(
            (tmp_path / "_agent" / "plugin-worker" / "hooks" / "hooks.json").read_text()
        )
        assert "Stop" not in data["hooks"]

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

    def test_plugin_dir_role_specific(self, tmp_path):
        """With role, plugin dir is _agent/plugin-{role}/ and --plugin-dir points there."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        cmd = claude.build_command(
            role="reviewer",
            rules_path=rules,
            project_root=tmp_path,
        )
        expected = tmp_path / "_agent" / "plugin-reviewer"
        assert expected.exists()
        idx = cmd.index("--plugin-dir")
        assert Path(cmd[idx + 1]) == expected

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

    def test_empty_path_with_none_raises(self):
        """Path('') with None should raise ValueError."""
        claude = Claude()
        with pytest.raises(ValueError, match="must both be provided together"):
            claude.build_command(rules_path=Path(""), project_root=None)

    def test_headless_mode(self, tmp_path):
        """With prompt, -p, --output-format json, and --no-session-persistence are added."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        cmd = claude.build_command(
            prompt="Do the work",
            role="worker",
            rules_path=rules,
            project_root=tmp_path,
        )
        assert "-p" in cmd
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "Do the work"
        assert "--output-format" in cmd
        fmt_idx = cmd.index("--output-format")
        assert cmd[fmt_idx + 1] == "json"
        assert "--no-session-persistence" in cmd

    def test_headless_worker_has_json_schema(self, tmp_path):
        """Worker headless mode includes --json-schema with correct structure."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        cmd = claude.build_command(
            prompt="Do the work",
            role="worker",
            rules_path=rules,
            project_root=tmp_path,
        )
        assert "--json-schema" in cmd
        schema_idx = cmd.index("--json-schema")
        schema = json.loads(cmd[schema_idx + 1])
        assert schema["type"] == "object"
        assert "ticket_id" in schema["properties"]
        assert "status" in schema["properties"]
        assert schema["properties"]["status"]["enum"] == ["verify", "in_progress"]
        assert "notes" in schema["properties"]

    def test_headless_reviewer_has_json_schema(self, tmp_path):
        """Reviewer headless mode includes --json-schema with result/feedback/criteria fields."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        cmd = claude.build_command(
            prompt="Review the work",
            role="reviewer",
            rules_path=rules,
            project_root=tmp_path,
        )
        assert "--json-schema" in cmd
        schema_idx = cmd.index("--json-schema")
        schema = json.loads(cmd[schema_idx + 1])
        assert "result" in schema["properties"]
        assert schema["properties"]["result"]["enum"] == ["pass", "fail"]
        assert "feedback" in schema["properties"]
        assert "criteria" in schema["properties"]
        assert schema["properties"]["criteria"]["type"] == "array"
        assert "criteria" in schema["required"]

    def test_headless_no_role_no_schema(self, tmp_path):
        """Headless mode without a role does not include --json-schema."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        cmd = claude.build_command(
            prompt="Do something",
            rules_path=rules,
            project_root=tmp_path,
        )
        assert "-p" in cmd
        assert "--json-schema" not in cmd

    def test_interactive_mode_no_prompt_flags(self):
        """Without prompt, no -p, --output-format, or --no-session-persistence flags."""
        claude = Claude()
        cmd = claude.build_command()
        assert "-p" not in cmd
        assert "--output-format" not in cmd
        assert "--no-session-persistence" not in cmd
        assert "--json-schema" not in cmd

    def test_ticket_mcp_in_plugin(self, tmp_path):
        """Plugin .mcp.json includes both tickets and coderabbit entries."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        mcp = json.loads(
            (tmp_path / "_agent" / "plugin" / ".mcp.json").read_text()
        )

        assert "mcpServers" in mcp
        assert "coderabbit" in mcp["mcpServers"]
        assert "tickets" in mcp["mcpServers"]

        cr = mcp["mcpServers"]["coderabbit"]
        assert cr["type"] == "stdio"
        assert "coderabbit_mcp" in " ".join(cr["args"])

        ts = mcp["mcpServers"]["tickets"]
        assert ts["type"] == "stdio"
        assert "--project-root" in ts["args"]
        assert "ticket_mcp" in " ".join(ts["args"])

    def test_ticket_mcp_has_project_root(self, tmp_path):
        """Ticket MCP config passes the correct --project-root."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        mcp = json.loads(
            (tmp_path / "_agent" / "plugin" / ".mcp.json").read_text()
        )

        ts_args = mcp["mcpServers"]["tickets"]["args"]
        root_idx = ts_args.index("--project-root")
        assert tmp_path.as_posix() in ts_args[root_idx + 1]

    def test_role_forwarded_to_ticket_mcp(self, tmp_path):
        """When role is provided, --role appears in ticket MCP args."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            role="worker",
            rules_path=rules,
            project_root=tmp_path,
        )
        mcp = json.loads(
            (tmp_path / "_agent" / "plugin-worker" / ".mcp.json").read_text()
        )

        ts_args = mcp["mcpServers"]["tickets"]["args"]
        assert "--role" in ts_args
        role_idx = ts_args.index("--role")
        assert ts_args[role_idx + 1] == "worker"

    def test_no_role_no_role_in_ticket_mcp(self, tmp_path):
        """When no role is given, --role is absent from ticket MCP args."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        mcp = json.loads(
            (tmp_path / "_agent" / "plugin" / ".mcp.json").read_text()
        )

        ts_args = mcp["mcpServers"]["tickets"]["args"]
        assert "--role" not in ts_args

    def test_max_turns_in_headless(self, tmp_path):
        """max_turns adds --max-turns flag in headless mode."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        cmd = claude.build_command(
            prompt="Do work",
            role="worker",
            rules_path=rules,
            project_root=tmp_path,
            tool_config={"max_turns": 25},
        )
        assert "--max-turns" in cmd
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "25"

    def test_no_max_turns_by_default(self, tmp_path):
        """Without max_turns, --max-turns is not added."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        cmd = claude.build_command(
            prompt="Do work",
            role="worker",
            rules_path=rules,
            project_root=tmp_path,
        )
        assert "--max-turns" not in cmd

    def test_max_turns_not_in_interactive(self):
        """max_turns is ignored in interactive mode (no prompt)."""
        claude = Claude()
        cmd = claude.build_command(tool_config={"max_turns": 25})
        assert "--max-turns" not in cmd

    def test_permission_request_hook_for_mcp(self, tmp_path):
        """PermissionRequest hook auto-approves MCP tools via approve_mcp."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        hooks_path = tmp_path / "_agent" / "plugin" / "hooks" / "hooks.json"
        data = json.loads(hooks_path.read_text())

        perm = data["hooks"]["PermissionRequest"]
        assert len(perm) == 1
        assert perm[0]["matcher"] == "^mcp__"
        hook_cmd = perm[0]["hooks"][0]["command"]
        assert "approve_mcp" in hook_cmd
        assert "repo_tools.agent.hooks" in hook_cmd

    def test_lint_mcp_in_plugin(self, tmp_path):
        """Plugin .mcp.json includes a lint server entry."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        mcp = json.loads(
            (tmp_path / "_agent" / "plugin" / ".mcp.json").read_text()
        )

        assert "lint" in mcp["mcpServers"]
        lint = mcp["mcpServers"]["lint"]
        assert lint["type"] == "stdio"
        assert "lint_mcp_stdio" in " ".join(lint["args"])

    def test_lint_mcp_no_select_by_default(self, tmp_path):
        """Without ruff_select, --select does not appear in lint MCP args."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        mcp = json.loads(
            (tmp_path / "_agent" / "plugin" / ".mcp.json").read_text()
        )

        lint_args = mcp["mcpServers"]["lint"]["args"]
        assert "--select" not in lint_args

    def test_lint_mcp_select_passed_through(self, tmp_path):
        """ruff_select adds --select to lint MCP args."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
            tool_config={"ruff_select": "E,F,S,B,SIM"},
        )
        mcp = json.loads(
            (tmp_path / "_agent" / "plugin" / ".mcp.json").read_text()
        )

        lint_args = mcp["mcpServers"]["lint"]["args"]
        assert "--select" in lint_args
        select_idx = lint_args.index("--select")
        assert lint_args[select_idx + 1] == "E,F,S,B,SIM"

    def test_lint_mcp_ignore_passed_through(self, tmp_path):
        """ruff_ignore adds --ignore to lint MCP args."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
            tool_config={"ruff_ignore": "SIM108,B006"},
        )
        mcp = json.loads(
            (tmp_path / "_agent" / "plugin" / ".mcp.json").read_text()
        )

        lint_args = mcp["mcpServers"]["lint"]["args"]
        assert "--ignore" in lint_args
        ignore_idx = lint_args.index("--ignore")
        assert lint_args[ignore_idx + 1] == "SIM108,B006"

    def test_lint_mcp_no_ignore_by_default(self, tmp_path):
        """Without ruff_ignore, --ignore does not appear in lint MCP args."""
        rules = tmp_path / "rules.toml"
        rules.write_text('default_reason = "no"\n', encoding="utf-8")

        claude = Claude()
        claude.build_command(
            rules_path=rules,
            project_root=tmp_path,
        )
        mcp = json.loads(
            (tmp_path / "_agent" / "plugin" / ".mcp.json").read_text()
        )

        lint_args = mcp["mcpServers"]["lint"]["args"]
        assert "--ignore" not in lint_args
