"""Tests for the approval logic in repo_tools.agent.approver."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from repo_tools.agent.approver import AutoApprover, _extract_commands, load_rules
from repo_tools.agent.runner import ToolRequest


# ── Helpers ────────────────────────────────────────────────────────────


def _default_rules_path() -> Path:
    """Return the path to the default rules TOML shipped with the package."""
    from repo_tools.agent import approver
    return Path(approver.__file__).resolve().parent / "rules_default.toml"


def _make_approver(rules_path: Path | None = None, project_root: Path | None = None) -> AutoApprover:
    """Build an AutoApprover with mocked backend and session."""
    backend = MagicMock()
    session = MagicMock()
    path = rules_path or _default_rules_path()
    return AutoApprover(
        backend=backend,
        session=session,
        rules_path=path,
        project_root=project_root,
    )


# ── load_rules ─────────────────────────────────────────────────────────


class TestLoadRules:
    def test_load_rules_default(self):
        """Loading rules_default.toml yields deny rules, allow rules, and a default_reason."""
        rules = load_rules(_default_rules_path())

        assert rules.default_reason, "default_reason should be a non-empty string"
        assert len(rules.deny) > 0, "expected at least one deny rule"
        assert len(rules.allow) > 0, "expected at least one allow rule"


# ── _extract_commands ──────────────────────────────────────────────────


class TestExtractCommands:
    def test_extract_commands_simple(self):
        """A single command returns a one-element list."""
        result = _extract_commands("git status")
        assert result == ["git status"]

    def test_extract_commands_chained(self):
        """Commands joined with && are split into separate entries."""
        result = _extract_commands("git add . && git commit -m 'msg'")
        assert len(result) == 2
        assert result[0].startswith("git add")
        assert result[1].startswith("git commit")

    def test_extract_commands_pipe(self):
        """Piped commands are split into separate entries."""
        result = _extract_commands("cat file | grep pattern")
        assert len(result) == 2
        assert "cat" in result[0]
        assert "grep" in result[1]

    def test_extract_commands_with_assignment(self):
        """Leading variable assignments are stripped, leaving the actual command."""
        result = _extract_commands("FOO=bar echo hello")
        assert result == ["echo hello"]


# ── AutoApprover._check_request ───────────────────────────────────────


class TestCheckRequest:
    def test_check_request_non_bash_allowed(self):
        """Non-Bash tool requests are always allowed regardless of rules."""
        approver = _make_approver()
        allowed, reason = approver._check_request(ToolRequest(tool="Edit", command=None))
        assert allowed is True
        assert reason == ""

    def test_deny_rule_matches(self):
        """A command matching a deny rule (e.g. sudo) is denied."""
        approver = _make_approver()
        allowed, reason = approver._check_request(
            ToolRequest(tool="Bash", command="sudo apt install foo"),
        )
        assert allowed is False
        assert "privilege" in reason.lower() or "not permitted" in reason.lower()

    def test_allow_rule_matches(self):
        """A command matching an allow rule (e.g. git) is allowed."""
        approver = _make_approver()
        allowed, reason = approver._check_request(
            ToolRequest(tool="Bash", command="git status"),
        )
        assert allowed is True
        assert reason == ""

    def test_unknown_command_denied(self):
        """A command that matches no allow rule is denied."""
        approver = _make_approver()
        allowed, reason = approver._check_request(
            ToolRequest(tool="Bash", command="malicious_tool --evil"),
        )
        assert allowed is False

    def test_empty_command_denied(self):
        """A Bash request with no command is denied."""
        approver = _make_approver()
        allowed, reason = approver._check_request(
            ToolRequest(tool="Bash", command=None),
        )
        assert allowed is False

    def test_empty_string_command_denied(self):
        """A Bash request with an empty-string command is denied."""
        approver = _make_approver()
        allowed, reason = approver._check_request(
            ToolRequest(tool="Bash", command=""),
        )
        assert allowed is False
