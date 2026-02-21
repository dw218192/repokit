"""Tests for uncovered paths in repo_tools.agent.rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_tools.agent.rules import (
    Rule,
    RuleSet,
    _check_dir_constraint,
    _extract_commands,
    _extract_commands_regex,
    check_command,
    load_rules,
)

import re


# ── _check_dir_constraint ─────────────────────────────────────────


class TestCheckDirConstraint:
    def test_inside_project_root(self):
        root = Path("/project")
        cwd = Path("/project/src")
        assert _check_dir_constraint("project_root", root, cwd) is True

    def test_outside_project_root(self):
        root = Path("/project")
        cwd = Path("/other/dir")
        assert _check_dir_constraint("project_root", root, cwd) is False

    def test_negated_inside(self):
        root = Path("/project")
        cwd = Path("/project/src")
        assert _check_dir_constraint("!project_root", root, cwd) is False

    def test_negated_outside(self):
        root = Path("/project")
        cwd = Path("/other")
        assert _check_dir_constraint("!project_root", root, cwd) is True

    def test_no_project_root_returns_true(self):
        assert _check_dir_constraint("project_root", None, Path("/x")) is True

    def test_no_cwd_returns_true(self):
        assert _check_dir_constraint("project_root", Path("/x"), None) is True

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="Unknown dir_spec"):
            _check_dir_constraint("unknown_thing", Path("/x"), Path("/y"))


# ── _extract_commands fallback ────────────────────────────────────


class TestExtractCommandsFallback:
    def test_semicolon_separated(self):
        result = _extract_commands("echo a ; echo b")
        assert len(result) == 2

    def test_pipe_chain(self):
        result = _extract_commands("cat file | grep foo | wc -l")
        assert len(result) == 3

    def test_variable_assignment_stripped(self):
        result = _extract_commands("FOO=bar BAZ=1 echo hello")
        assert result == ["echo hello"]

    def test_empty_returns_empty(self):
        result = _extract_commands("")
        assert result == []

    def test_complex_heredoc(self):
        # This exercises the quoted heredoc regex normalization
        cmd = """cat <<'EOF'\nhello\nEOF"""
        result = _extract_commands(cmd)
        assert len(result) >= 1

    def test_compound_and_or(self):
        result = _extract_commands("test -f foo && echo yes || echo no")
        assert len(result) == 3


# ── check_command with dir constraints ────────────────────────────


class TestCheckCommandDirConstraints:
    def _make_rules(self, allow_dir=None, deny_dir=None):
        return RuleSet(
            default_reason="not allowed",
            deny=[
                Rule(
                    name="rm",
                    patterns=[re.compile(r"^rm\b")],
                    reason="dangerous",
                    dir=deny_dir,
                ),
            ],
            allow=[
                Rule(
                    name="echo",
                    patterns=[re.compile(r"^echo\b")],
                    dir=allow_dir,
                ),
            ],
        )

    def test_allow_rule_dir_constraint_passes(self):
        rules = self._make_rules(allow_dir="project_root")
        allowed, reason = check_command(
            "echo hi", rules,
            project_root=Path("/proj"),
            cwd=Path("/proj/src"),
        )
        assert allowed is True

    def test_allow_rule_dir_constraint_fails(self):
        rules = self._make_rules(allow_dir="project_root")
        allowed, reason = check_command(
            "echo hi", rules,
            project_root=Path("/proj"),
            cwd=Path("/other"),
        )
        assert allowed is False

    def test_deny_rule_dir_constraint_applies(self):
        rules = self._make_rules(deny_dir="project_root")
        allowed, reason = check_command(
            "rm -rf .", rules,
            project_root=Path("/proj"),
            cwd=Path("/proj/src"),
        )
        assert allowed is False
        assert reason == "dangerous"

    def test_deny_rule_dir_constraint_skipped(self):
        # rm outside project_root — deny rule doesn't apply, but still denied by default
        rules = self._make_rules(deny_dir="project_root")
        allowed, reason = check_command(
            "rm -rf .", rules,
            project_root=Path("/proj"),
            cwd=Path("/other"),
        )
        # rm doesn't match any allow rule, so denied by default
        assert allowed is False

    def test_no_commands_parsed(self):
        rules = self._make_rules()
        allowed, reason = check_command("   ", rules)
        assert allowed is False


# ── bashlex-aware _extract_commands ──────────────────────────────


class TestExtractCommandsBashlex:
    def test_quoted_operator_not_split(self):
        """Quoted && inside echo should not be treated as a command separator."""
        result = _extract_commands('echo "foo && bar"')
        assert len(result) == 1
        assert "&&" in result[0]

    def test_subshell_visible(self):
        """Subshell $(...) commands are extracted."""
        result = _extract_commands("echo $(whoami)")
        assert len(result) >= 1

    def test_mixed_quoted_and_unquoted(self):
        """Real && separates commands but quoted && does not."""
        result = _extract_commands('echo "a && b" && ls')
        assert len(result) == 2

    def test_parse_error_falls_back_to_regex(self):
        """Unparseable input falls back to regex splitting."""
        # Deliberately broken syntax that bashlex cannot parse
        result = _extract_commands("echo <(bad syntax &&& ) && ls")
        # Should still produce some result via regex fallback
        assert len(result) >= 1

    def test_variable_assignment_stripped_bashlex(self):
        """Variable assignments are stripped even with bashlex parsing."""
        result = _extract_commands("FOO=bar echo hello")
        assert result == ["echo hello"]


# ── ENV=... cmd through full pipeline ────────────────────────────


class TestEnvPrefixExtraction:
    """bashlex strips quoted ``VAR=value`` prefixes via AST word nodes."""

    def test_double_quoted_spaces(self):
        result = _extract_commands('FOO="hello world" echo hi')
        assert result == ["echo hi"]

    def test_single_quoted_spaces(self):
        result = _extract_commands("FOO='hello world' echo hi")
        assert result == ["echo hi"]

    def test_multi_env_quoted(self):
        result = _extract_commands('A="x y" B=z echo hi')
        assert result == ["echo hi"]

    def test_env_prefix_with_compound(self):
        result = _extract_commands('FOO="a b" echo hi && BAR=1 ls')
        assert len(result) == 2
        assert result[0] == "echo hi"
        assert result[1] == "ls"

    def test_bare_assignment_ignored(self):
        result = _extract_commands("FOO=bar")
        assert result == []
