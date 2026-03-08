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


def _run_approve_ticket(tool_input: dict, required_criteria: list[str] | None = None) -> dict:
    """Run the approve_ticket hook with a synthetic PreToolUse event."""
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__plugin_repokit-agent_tickets__create_ticket",
        "tool_input": tool_input,
    }
    cmd = [
        sys.executable, "-m", "repo_tools.agent.hooks",
        "approve_ticket",
        "--required-criteria", json.dumps(required_criteria or []),
    ]
    result = subprocess.run(
        cmd,
        input=json.dumps(event),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Hook exited {result.returncode}: {result.stderr}"
    return json.loads(result.stdout)


class TestApproveTicketHook:
    def test_returns_ask_decision(self):
        """Hook returns permissionDecision 'ask' to trigger native prompt."""
        output = _run_approve_ticket({"id": "t1", "title": "Test", "criteria": []})
        specific = output["hookSpecificOutput"]
        assert specific["permissionDecision"] == "ask"
        assert specific["hookEventName"] == "PreToolUse"

    def test_merges_required_criteria(self):
        """Required criteria are appended to the tool input criteria."""
        output = _run_approve_ticket(
            {"id": "t1", "title": "Test", "criteria": ["User criterion"]},
            required_criteria=["Must pass tests", "Must lint clean"],
        )
        merged = output["hookSpecificOutput"]["updatedInput"]["criteria"]
        assert merged == ["User criterion", "Must pass tests", "Must lint clean"]

    def test_deduplicates_criteria(self):
        """Criteria already present are not duplicated."""
        output = _run_approve_ticket(
            {"id": "t1", "criteria": ["Must pass tests", "Other"]},
            required_criteria=["Must pass tests", "New one"],
        )
        merged = output["hookSpecificOutput"]["updatedInput"]["criteria"]
        assert merged == ["Must pass tests", "Other", "New one"]

    def test_preserves_other_fields(self):
        """Non-criteria fields in tool_input are passed through."""
        output = _run_approve_ticket(
            {"id": "my-ticket", "title": "My Title", "description": "Desc", "criteria": []},
        )
        updated = output["hookSpecificOutput"]["updatedInput"]
        assert updated["id"] == "my-ticket"
        assert updated["title"] == "My Title"
        assert updated["description"] == "Desc"

    def test_no_required_criteria(self):
        """With no required criteria, tool input is unchanged."""
        output = _run_approve_ticket(
            {"id": "t1", "criteria": ["A", "B"]},
            required_criteria=[],
        )
        merged = output["hookSpecificOutput"]["updatedInput"]["criteria"]
        assert merged == ["A", "B"]
