"""Tests for the PreToolUse hook script."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _rules_path() -> Path:
    from repo_tools.agent import rules as rules_mod
    return Path(rules_mod.__file__).resolve().parent / "allowlist_default.toml"


def _run_hook(command: str, rules: Path | None = None, cwd: str = "/tmp") -> dict:
    """Run the hook script with a synthetic PreToolUse event and return the output."""
    rules = rules or _rules_path()
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": cwd,
    }
    result = subprocess.run(
        [sys.executable, "-m", "repo_tools.agent.hooks.check_bash",
         "--rules", str(rules), "--project-root", cwd],
        input=json.dumps(event),
        capture_output=True,
        text=True,
    )
    if result.returncode == 2:
        pytest.fail(f"Hook error: {result.stderr}")
    assert result.returncode == 0, f"Hook exited {result.returncode}: {result.stderr}"
    return json.loads(result.stdout)


class TestUnifiedEntrypoint:
    """Test that python -m repo_tools.agent.hooks dispatches correctly."""

    def test_check_bash_via_unified_entrypoint(self):
        """check_bash subcommand works via the unified entrypoint."""
        rules = _rules_path()
        event = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "cwd": "/tmp",
        }
        result = subprocess.run(
            [sys.executable, "-m", "repo_tools.agent.hooks",
             "check_bash", "--rules", str(rules), "--project-root", "/tmp"],
            input=json.dumps(event),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Hook exited {result.returncode}: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_unknown_subcommand_exits_2(self):
        """Unknown subcommand exits with code 2."""
        result = subprocess.run(
            [sys.executable, "-m", "repo_tools.agent.hooks", "bogus"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2

    def test_no_subcommand_exits_2(self):
        """No subcommand exits with code 2."""
        result = subprocess.run(
            [sys.executable, "-m", "repo_tools.agent.hooks"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2


class TestHookAllow:
    def test_git_allowed(self):
        output = _run_hook("git status")
        decision = output["hookSpecificOutput"]["permissionDecision"]
        assert decision == "allow"

    def test_ls_allowed(self):
        output = _run_hook("ls -la")
        decision = output["hookSpecificOutput"]["permissionDecision"]
        assert decision == "allow"


class TestHookDeny:
    def test_sudo_denied(self):
        output = _run_hook("sudo rm -rf /")
        specific = output["hookSpecificOutput"]
        assert specific["permissionDecision"] == "deny"
        assert "Blocked" in specific["permissionDecisionReason"]

    def test_unknown_command_denied(self):
        output = _run_hook("evil_binary --hack")
        specific = output["hookSpecificOutput"]
        assert specific["permissionDecision"] == "deny"

    def test_empty_command_denied(self):
        output = _run_hook("")
        specific = output["hookSpecificOutput"]
        assert specific["permissionDecision"] == "deny"
