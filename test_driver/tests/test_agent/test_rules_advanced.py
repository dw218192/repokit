"""Tests for uncovered paths in repo_tools.agent.rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_tools.agent.rules import (
    Rule,
    RuleSet,
    _check_dir_constraint,
    _collapse_subshells,
    _extract_commands,
    _extract_commands_regex,
    _strip_heredoc_quotes,
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


# ── override_deny ────────────────────────────────────────────────


class TestOverrideDeny:
    """Allow rules with override_deny=True can override matching deny rules."""

    def _make_rules(self, override_deny=False):
        return RuleSet(
            default_reason="not allowed",
            deny=[
                Rule(
                    name="git_rewrite",
                    patterns=[re.compile(r"git\s+commit\s.*--amend")],
                    reason="history rewrite blocked",
                ),
            ],
            allow=[
                Rule(
                    name="git",
                    patterns=[re.compile(r"^git\b")],
                ),
                Rule(
                    name="safe_rewrite",
                    patterns=[re.compile(r"git\s+commit\s.*--amend")],
                    override_deny=override_deny,
                ),
            ],
        )

    def test_deny_wins_without_override(self):
        rules = self._make_rules(override_deny=False)
        allowed, reason = check_command("git commit --amend -m fix", rules)
        assert allowed is False
        assert reason == "history rewrite blocked"

    def test_allow_overrides_deny(self):
        rules = self._make_rules(override_deny=True)
        allowed, reason = check_command("git commit --amend -m fix", rules)
        assert allowed is True

    def test_generic_allow_does_not_override(self):
        """A broad ^git allow without override_deny cannot override deny."""
        rules = RuleSet(
            default_reason="not allowed",
            deny=[
                Rule(
                    name="git_rewrite",
                    patterns=[re.compile(r"git\s+push\s.*--force")],
                    reason="force push blocked",
                ),
            ],
            allow=[
                Rule(
                    name="git",
                    patterns=[re.compile(r"^git\b")],
                    override_deny=False,
                ),
            ],
        )
        allowed, reason = check_command("git push --force", rules)
        assert allowed is False

    def test_override_respects_dir_constraint(self):
        """override_deny allow rule with dir constraint that fails does not override."""
        rules = RuleSet(
            default_reason="not allowed",
            deny=[
                Rule(
                    name="rm_deny",
                    patterns=[re.compile(r"^rm\b")],
                    reason="rm blocked",
                ),
            ],
            allow=[
                Rule(
                    name="rm_in_project",
                    patterns=[re.compile(r"^rm\b")],
                    override_deny=True,
                    dir="project_root",
                ),
            ],
        )
        # Outside project root — override should not apply
        allowed, reason = check_command(
            "rm -rf foo", rules,
            project_root=Path("/proj"),
            cwd=Path("/other"),
        )
        assert allowed is False

    def test_override_with_compound_command(self):
        """All denied subcommands must be overridden for the compound command to pass."""
        rules = RuleSet(
            default_reason="not allowed",
            deny=[
                Rule(
                    name="git_rewrite",
                    patterns=[re.compile(r"git\s+commit\s.*--amend")],
                    reason="amend blocked",
                ),
            ],
            allow=[
                Rule(
                    name="git",
                    patterns=[re.compile(r"^git\b")],
                ),
                Rule(
                    name="safe_amend",
                    patterns=[re.compile(r"git\s+commit\s.*--amend")],
                    override_deny=True,
                ),
            ],
        )
        allowed, reason = check_command("git add . && git commit --amend -m fix", rules)
        assert allowed is True


# ── heredoc helpers ──────────────────────────────────────────────


class TestStripHeredocQuotes:
    def test_single_quoted(self):
        assert _strip_heredoc_quotes("<<'EOF'") == "<<EOF"

    def test_double_quoted(self):
        assert _strip_heredoc_quotes('<<"EOF"') == "<<EOF"

    def test_dash_variant(self):
        assert _strip_heredoc_quotes("<<-'EOF'") == "<<-EOF"

    def test_unquoted_unchanged(self):
        assert _strip_heredoc_quotes("<<EOF") == "<<EOF"

    def test_inside_larger_command(self):
        cmd = "git commit -m \"$(cat <<'EOF'\nmsg\nEOF\n)\""
        result = _strip_heredoc_quotes(cmd)
        assert "<<EOF" in result
        assert "<<'EOF'" not in result


class TestCollapseSubshells:
    def test_single_subshell(self):
        assert _collapse_subshells("echo $(whoami)") == 'echo "_subshell_"'

    def test_nested_subshells(self):
        result = _collapse_subshells("echo $(cat $(ls))")
        assert result == 'echo "_subshell_"'

    def test_no_subshell(self):
        assert _collapse_subshells("echo hello") == "echo hello"

    def test_multiple_subshells(self):
        result = _collapse_subshells("$(a) && $(b)")
        assert result == '"_subshell_" && "_subshell_"'

    def test_operators_inside_subshell_hidden(self):
        result = _collapse_subshells("git commit -m $(cat <<EOF a && b EOF)")
        assert "&&" not in result


# ── heredoc integration ──────────────────────────────────────────


class TestHeredocCommands:
    """Verify that heredoc commit patterns are parsed correctly."""

    def test_unquoted_delimiter(self):
        cmd = 'git commit -m "$(cat <<EOF\nCommit message.\nEOF\n)"'
        result = _extract_commands(cmd)
        assert len(result) >= 1
        assert result[0].startswith("git ")

    def test_quoted_delimiter(self):
        cmd = "git commit -m \"$(cat <<'EOF'\nCommit message.\nEOF\n)\""
        result = _extract_commands(cmd)
        assert len(result) >= 1
        assert result[0].startswith("git ")

    def test_pipe_in_message_body(self):
        cmd = "git commit -m \"$(cat <<'EOF'\nFix build | test pipeline\nEOF\n)\""
        result = _extract_commands(cmd)
        assert any(c.startswith("git ") for c in result)

    def test_semicolons_in_message_body(self):
        cmd = "git commit -m \"$(cat <<'EOF'\nFix: init; cleanup; teardown\nEOF\n)\""
        result = _extract_commands(cmd)
        assert any(c.startswith("git ") for c in result)

    def test_ampersand_in_message_body(self):
        cmd = "git commit -m \"$(cat <<'EOF'\nFix build && add tests\nEOF\n)\""
        result = _extract_commands(cmd)
        assert any(c.startswith("git ") for c in result)

    def test_multiline_message(self):
        cmd = "git commit -m \"$(cat <<'EOF'\n## Summary\n- Fix build | pipeline\n- Tests && coverage\nEOF\n)\""
        result = _extract_commands(cmd)
        assert any(c.startswith("git ") for c in result)


class TestHeredocRegexFallback:
    """Verify the regex fallback handles heredoc content safely."""

    def test_pipe_not_split(self):
        cmd = "git commit -m \"$(cat <<'EOF'\nFix | test\nEOF\n)\""
        result = _extract_commands_regex(cmd)
        assert len(result) == 1
        assert result[0].startswith("git ")

    def test_ampersand_not_split(self):
        cmd = "git commit -m \"$(cat <<'EOF'\nFix && test\nEOF\n)\""
        result = _extract_commands_regex(cmd)
        assert len(result) == 1
        assert result[0].startswith("git ")

    def test_real_compound_still_splits(self):
        result = _extract_commands_regex("git add -A && git commit -m 'msg'")
        assert len(result) == 2


class TestHeredocCheckCommand:
    """End-to-end: heredoc commit passes the full allowlist."""

    @staticmethod
    def _git_rules():
        return RuleSet(
            default_reason="not allowed",
            deny=[],
            allow=[
                Rule(name="git", patterns=[re.compile(r"^git\b")]),
                Rule(name="cat", patterns=[re.compile(r"^cat\b")]),
            ],
        )

    def test_simple_heredoc_allowed(self):
        cmd = "git commit -m \"$(cat <<'EOF'\nFix stuff\nEOF\n)\""
        allowed, _ = check_command(cmd, self._git_rules())
        assert allowed is True

    def test_heredoc_with_operators_allowed(self):
        cmd = "git commit -m \"$(cat <<'EOF'\nFix build && add | tests; done\nEOF\n)\""
        allowed, _ = check_command(cmd, self._git_rules())
        assert allowed is True
