"""Tests for rule-based permission checking in repo_tools.agent.rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_tools.agent.rules import _extract_commands, check_command, load_rules


# ── Helpers ────────────────────────────────────────────────────────────


def _default_rules_path() -> Path:
    """Return the path to the default rules TOML shipped with the package."""
    from repo_tools.agent import rules as rules_mod

    return Path(rules_mod.__file__).resolve().parent / "allowlist_default.toml"


def _default_rules():
    return load_rules(_default_rules_path())


# ── load_rules ─────────────────────────────────────────────────────────


class TestLoadRules:
    def test_load_rules_default(self):
        """Loading allowlist_default.toml yields deny rules, allow rules, and a default_reason."""
        rules = _default_rules()

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


# ── check_command ─────────────────────────────────────────────────────


class TestCheckCommand:
    def test_deny_rule_matches(self):
        """A command matching a deny rule (e.g. sudo) is denied."""
        rules = _default_rules()
        allowed, reason = check_command("sudo apt install foo", rules)
        assert allowed is False
        assert "privilege" in reason.lower() or "not permitted" in reason.lower()

    def test_allow_rule_matches(self):
        """A command matching an allow rule (e.g. git) is allowed."""
        rules = _default_rules()
        allowed, reason = check_command("git status", rules)
        assert allowed is True
        assert reason == ""

    def test_unknown_command_denied(self):
        """A command that matches no allow rule is denied."""
        rules = _default_rules()
        allowed, reason = check_command("malicious_tool --evil", rules)
        assert allowed is False

    def test_empty_command_denied(self):
        """A None command is denied."""
        rules = _default_rules()
        allowed, reason = check_command(None, rules)
        assert allowed is False

    def test_empty_string_command_denied(self):
        """An empty-string command is denied."""
        rules = _default_rules()
        allowed, reason = check_command("", rules)
        assert allowed is False


# ── Role-filtered rules ────────────────────────────────────────────────


class TestRoleFilteredRules:
    def test_agent_dir_denied_for_worker(self):
        """agent_dir deny rule blocks _agent/ access when role=worker."""
        rules = load_rules(_default_rules_path(), role="worker")
        # Commands touching _agent/ should be denied
        allowed, reason = check_command("cat _agent/ws1/tickets/G1_1.toml", rules)
        assert allowed is False
        assert "_agent/" in reason or "relay" in reason

    def test_agent_dir_denied_for_reviewer(self):
        """agent_dir deny rule blocks _agent/ access when role=reviewer."""
        rules = load_rules(_default_rules_path(), role="reviewer")
        allowed, reason = check_command("cat _agent/ws1/tickets/G1_1.toml", rules)
        assert allowed is False

    def test_agent_dir_allowed_for_orchestrator(self):
        """agent_dir deny rule is filtered out for orchestrator — _agent/ writes are permitted."""
        rules = load_rules(_default_rules_path(), role="orchestrator")
        # The agent_dir deny rule has roles=["worker","reviewer"], so it won't apply to orchestrator.
        # The cat command itself must still match an allow rule.
        allowed, reason = check_command("cat _agent/ws1/tickets/G1_1.toml", rules)
        assert allowed is True  # allowed by file_inspection rule; agent_dir deny skipped

    def test_roles_field_filters_rules(self, tmp_path):
        """load_rules skips rules whose roles list excludes the given role."""
        rules_toml = tmp_path / "rules.toml"
        rules_toml.write_text(
            '[default_reason]\n'
            'default_reason = "blocked"\n\n'
            '[[deny]]\n'
            'name = "worker_only"\n'
            'patterns = ["^secret"]\n'
            'roles = ["worker"]\n'
            'reason = "worker deny"\n\n'
            '[[allow]]\n'
            'name = "all"\n'
            'patterns = [".+"]\n',
            encoding="utf-8",
        )
        # For role=worker: deny rule applies
        worker_rules = load_rules(rules_toml, role="worker")
        assert any(r.name == "worker_only" for r in worker_rules.deny)

        # For role=orchestrator: deny rule is filtered out
        orch_rules = load_rules(rules_toml, role="orchestrator")
        assert not any(r.name == "worker_only" for r in orch_rules.deny)

        # Without role: deny rule is filtered out (role=None not in ["worker"])
        no_role_rules = load_rules(rules_toml, role=None)
        assert not any(r.name == "worker_only" for r in no_role_rules.deny)
