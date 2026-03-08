"""Unit tests for in-process SDK hooks.

These tests exercise the hook functions directly (no SDK required).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repo_tools.agent.claude import _make_approve_mcp_hook, _make_check_bash_hook


@pytest.fixture
def rules_file(tmp_path):
    """Create a rules file with allow/deny rules."""
    rules = tmp_path / "rules.toml"
    rules.write_text(
        'default_reason = "not allowed"\n'
        '\n'
        '[[allow]]\n'
        'name = "safe_commands"\n'
        'commands = ["git", "ls", "cat"]\n'
        '\n'
        '[[deny]]\n'
        'name = "destructive"\n'
        'commands = ["rm"]\n'
        'reason = "destructive command"\n'
        '\n'
        '[[deny]]\n'
        'name = "elevated"\n'
        'commands = ["sudo"]\n'
        'reason = "elevated privileges"\n',
        encoding="utf-8",
    )
    return rules


# ── check_bash hook ──────────────────────────────────────────────


class TestCheckBashHook:
    def test_allows_git_status(self, rules_file, tmp_path):
        hook = _make_check_bash_hook(rules_file, tmp_path, role="worker")
        result = asyncio.run(hook(
            {"tool_input": {"command": "git status"}, "cwd": str(tmp_path)},
            None, {},
        ))
        assert result == {}

    def test_allows_git_log(self, rules_file, tmp_path):
        hook = _make_check_bash_hook(rules_file, tmp_path, role="worker")
        result = asyncio.run(hook(
            {"tool_input": {"command": "git log --oneline"}, "cwd": str(tmp_path)},
            None, {},
        ))
        assert result == {}

    def test_allows_ls(self, rules_file, tmp_path):
        hook = _make_check_bash_hook(rules_file, tmp_path, role="worker")
        result = asyncio.run(hook(
            {"tool_input": {"command": "ls -la"}, "cwd": str(tmp_path)},
            None, {},
        ))
        assert result == {}

    def test_denies_rm(self, rules_file, tmp_path):
        hook = _make_check_bash_hook(rules_file, tmp_path, role="worker")
        result = asyncio.run(hook(
            {"tool_input": {"command": "rm -rf /"}, "cwd": str(tmp_path)},
            None, {},
        ))
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "destructive" in result["hookSpecificOutput"]["permissionDecisionReason"]

    def test_denies_sudo(self, rules_file, tmp_path):
        hook = _make_check_bash_hook(rules_file, tmp_path, role="worker")
        result = asyncio.run(hook(
            {"tool_input": {"command": "sudo apt install foo"}, "cwd": str(tmp_path)},
            None, {},
        ))
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_unknown_command(self, rules_file, tmp_path):
        hook = _make_check_bash_hook(rules_file, tmp_path, role="worker")
        result = asyncio.run(hook(
            {"tool_input": {"command": "curl http://evil.com"}, "cwd": str(tmp_path)},
            None, {},
        ))
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_deny_reason_includes_rules_path(self, rules_file, tmp_path):
        hook = _make_check_bash_hook(rules_file, tmp_path, role="worker")
        result = asyncio.run(hook(
            {"tool_input": {"command": "rm foo"}, "cwd": str(tmp_path)},
            None, {},
        ))
        reason = result["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Rules:" in reason

    def test_no_role_filtering(self, rules_file, tmp_path):
        """Without role, rules without role filter still apply."""
        hook = _make_check_bash_hook(rules_file, tmp_path, role=None)
        result = asyncio.run(hook(
            {"tool_input": {"command": "git status"}, "cwd": str(tmp_path)},
            None, {},
        ))
        assert result == {}

    def test_uses_cwd_from_event(self, rules_file, tmp_path):
        """Hook reads cwd from input_data."""
        hook = _make_check_bash_hook(rules_file, tmp_path, role="worker")
        subdir = tmp_path / "sub"
        subdir.mkdir()
        result = asyncio.run(hook(
            {"tool_input": {"command": "git status"}, "cwd": str(subdir)},
            None, {},
        ))
        assert result == {}

    def test_defaults_cwd_to_dot(self, rules_file, tmp_path):
        """Missing cwd defaults to '.'."""
        hook = _make_check_bash_hook(rules_file, tmp_path, role="worker")
        result = asyncio.run(hook(
            {"tool_input": {"command": "git status"}},
            None, {},
        ))
        assert result == {}

    def test_role_specific_rules(self, tmp_path):
        """Rules with roles filter are respected."""
        rules = tmp_path / "rules.toml"
        rules.write_text(
            'default_reason = "no"\n'
            '[[allow]]\n'
            'name = "git"\n'
            'commands = ["git"]\n'
            '[[allow]]\n'
            'name = "make_worker"\n'
            'commands = ["make"]\n'
            'roles = ["worker"]\n',
            encoding="utf-8",
        )

        # Worker can use make
        hook_w = _make_check_bash_hook(rules, tmp_path, role="worker")
        result = asyncio.run(hook_w(
            {"tool_input": {"command": "make build"}, "cwd": str(tmp_path)},
            None, {},
        ))
        assert result == {}

        # Reviewer cannot use make (rule filtered by role)
        hook_r = _make_check_bash_hook(rules, tmp_path, role="reviewer")
        result = asyncio.run(hook_r(
            {"tool_input": {"command": "make build"}, "cwd": str(tmp_path)},
            None, {},
        ))
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── approve_mcp hook ─────────────────────────────────────────────


class TestApproveMcpHook:
    def test_auto_approves(self):
        hook = _make_approve_mcp_hook()
        result = asyncio.run(hook(
            {"tool_name": "mcp__repokit-agent__lint"},
            None, {},
        ))
        assert "hookSpecificOutput" in result
        output = result["hookSpecificOutput"]
        assert output["hookEventName"] == "PermissionRequest"
        assert output["decision"]["behavior"] == "allow"

    def test_approves_any_tool(self):
        hook = _make_approve_mcp_hook()
        result = asyncio.run(hook(
            {"tool_name": "mcp__anything__anything"},
            None, {},
        ))
        assert result["hookSpecificOutput"]["decision"]["behavior"] == "allow"

    def test_approves_empty_tool_name(self):
        hook = _make_approve_mcp_hook()
        result = asyncio.run(hook(
            {"tool_name": ""},
            None, {},
        ))
        assert result["hookSpecificOutput"]["decision"]["behavior"] == "allow"
