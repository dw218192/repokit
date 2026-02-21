"""Tests for the token system in repo_tools.core."""

from __future__ import annotations

import logging
import platform
from pathlib import Path

import pytest

from repo_tools.core import (
    TokenFormatter,
    _extract_references,
    _validate_token_graph,
    resolve_tokens,
)


# ── _extract_references ──────────────────────────────────────────────


class TestExtractReferences:
    """Unit tests for _extract_references()."""

    def test_simple_reference(self):
        assert _extract_references("{foo}") == {"foo"}

    def test_multiple_references(self):
        assert _extract_references("{a} and {b}") == {"a", "b"}

    def test_no_references(self):
        assert _extract_references("plain text") == set()

    def test_escaped_braces_ignored(self):
        assert _extract_references("{{not_a_ref}}") == set()

    def test_mixed_escaped_and_real(self):
        assert _extract_references("{{escaped}} {real}") == {"real"}


# ── _validate_token_graph ────────────────────────────────────────────


class TestTokenGraphValidation:
    """Unit tests for _validate_token_graph()."""

    def test_valid_graph(self):
        """No exception for a well-formed token graph."""
        _validate_token_graph({
            "base": "/opt",
            "full": "{base}/lib",
            "leaf": "no refs",
        })

    def test_self_reference(self):
        with pytest.raises(ValueError, match="references itself"):
            _validate_token_graph({"a": "x{a}"})

    def test_direct_cycle(self):
        with pytest.raises(ValueError, match="Circular token reference"):
            _validate_token_graph({"a": "{b}", "b": "{a}"})

    def test_transitive_cycle(self):
        with pytest.raises(ValueError, match="Circular token reference"):
            _validate_token_graph({"a": "{b}", "b": "{c}", "c": "{a}"})

    def test_missing_reference(self):
        with pytest.raises(KeyError, match="undefined token.*missing"):
            _validate_token_graph({"a": "{missing}"})

    def test_literal_braces_not_treated_as_references(self):
        """Escaped braces {{}} should not be treated as references."""
        _validate_token_graph({"a": "{{not_a_ref}}"})

    def test_diamond_dependency_no_false_positive(self):
        """Diamond: a->{b},{c}; b->{d}; c->{d} — valid, no cycle."""
        _validate_token_graph({
            "a": "{b} {c}",
            "b": "{d}",
            "c": "{d}",
            "d": "leaf",
        })


# ── TokenFormatter.resolve ───────────────────────────────────────────


class TestTokenFormatterResolve:
    """Unit tests for TokenFormatter.resolve()."""

    def test_simple_expansion(self):
        """Basic {key} placeholder resolves to its value."""
        fmt = TokenFormatter({"greeting": "hello", "name": "world"})
        assert fmt.resolve("{greeting}, {name}!") == "hello, world!"

    def test_cross_reference(self):
        """A token whose value contains another {token} is resolved recursively."""
        fmt = TokenFormatter(
            {
                "base": "/opt",
                "full": "{base}/lib",
            }
        )
        assert fmt.resolve("{full}") == "/opt/lib"

    def test_cycle_detection(self):
        """Circular references like {a}->{b}->{a} raise ValueError."""
        fmt = TokenFormatter(
            {
                "a": "{b}",
                "b": "{a}",
            }
        )
        with pytest.raises(ValueError, match="[Cc]ircular|exceeded"):
            fmt.resolve("{a}")

    def test_max_depth(self):
        """A chain deeper than MAX_DEPTH raises ValueError."""
        # Build a chain: t0 -> {t1} -> {t2} -> ... -> {tN}
        n = TokenFormatter.MAX_DEPTH + 5
        tokens = {f"t{i}": f"{{t{i + 1}}}" for i in range(n)}
        tokens[f"t{n}"] = "end"
        fmt = TokenFormatter(tokens)
        with pytest.raises(ValueError):
            fmt.resolve("{t0}")

    def test_missing_token(self):
        """Referencing an unknown token raises KeyError with a descriptive message."""
        fmt = TokenFormatter({"known": "yes"})
        with pytest.raises(KeyError, match="Missing token.*missing"):
            fmt.resolve("{missing}")

    def test_no_expansion_needed(self):
        """A string without curly-brace placeholders is returned unchanged."""
        fmt = TokenFormatter({"unused": "value"})
        plain = "no placeholders here"
        assert fmt.resolve(plain) == plain

    def test_self_reference_hits_max_depth(self):
        """Self-referencing token exhausts MAX_DEPTH with descriptive error."""
        fmt = TokenFormatter({"a": "x{a}"})
        with pytest.raises(ValueError, match="exceeded.*unresolved.*a"):
            fmt.resolve("{a}")

    def test_stable_template_returns_immediately(self):
        """A template that doesn't change after expansion returns in one pass."""
        fmt = TokenFormatter({"a": "hello"})
        assert fmt.resolve("{a} world") == "hello world"


# ── resolve_tokens ───────────────────────────────────────────────────


class TestResolveTokens:
    """Integration tests for resolve_tokens()."""

    def test_resolve_tokens_builtins(self, tmp_path: Path):
        """Built-in tokens (exe_ext, shell_ext, lib_ext, path_sep) are present."""
        tokens = resolve_tokens(str(tmp_path), {}, {})

        assert "exe_ext" in tokens
        assert "shell_ext" in tokens
        assert "lib_ext" in tokens
        assert "path_sep" in tokens

        # Sanity-check values based on current platform
        system = platform.system()
        if system == "Windows":
            assert tokens["exe_ext"] == ".exe"
            assert tokens["shell_ext"] == ".cmd"
            assert tokens["lib_ext"] == ".dll"
            assert tokens["path_sep"] == ";"
        elif system == "Darwin":
            assert tokens["exe_ext"] == ""
            assert tokens["shell_ext"] == ".sh"
            assert tokens["lib_ext"] == ".dylib"
            assert tokens["path_sep"] == ":"
        else:
            assert tokens["exe_ext"] == ""
            assert tokens["shell_ext"] == ".sh"
            assert tokens["lib_ext"] == ".so"
            assert tokens["path_sep"] == ":"

    def test_resolve_tokens_workspace_paths(self, tmp_path: Path):
        """Only workspace_root is framework-injected; build_root/logs_root are user-defined."""
        ws = tmp_path.as_posix()
        tokens = resolve_tokens(str(tmp_path), {}, {})

        assert tokens["workspace_root"] == ws
        assert "build_root" not in tokens  # user-defined, not framework-injected
        assert "logs_root" not in tokens   # user-defined, not framework-injected
        assert "build_dir" not in tokens   # user-defined, not framework-injected

    def test_resolve_tokens_config_tokens(self, tmp_path: Path):
        """Tokens declared in config['tokens'] appear in the result."""
        config = {
            "tokens": {
                "my_var": "custom_value",
                "another": "42",
            },
        }
        tokens = resolve_tokens(str(tmp_path), config, {})

        assert tokens["my_var"] == "custom_value"
        assert tokens["another"] == "42"

    def test_resolve_tokens_dimensions_override(self, tmp_path: Path):
        """Dimension values appear in the token dict."""
        dims = {"platform": "linux-arm64", "build_type": "Release"}
        tokens = resolve_tokens(str(tmp_path), {}, dims)

        assert tokens["platform"] == "linux-arm64"
        assert tokens["build_type"] == "Release"

    def test_resolve_tokens_user_defined_build_dir(self, tmp_path: Path):
        """build_dir can be defined as a cross-reference token by the user."""
        config = {"tokens": {
            "build_root": "_build",
            "build_dir": "{build_root}/{platform}/{build_type}",
        }}
        dims = {"platform": "linux-x64", "build_type": "Release"}
        tokens = resolve_tokens(str(tmp_path), config, dims)

        assert "linux-x64" in tokens["build_dir"]
        assert "Release" in tokens["build_dir"]

    def test_graph_validation_catches_typo(self, tmp_path: Path):
        """A typo like {buld_root} raises KeyError."""
        config = {"tokens": {"build_dir": "{buld_root}/out"}}
        with pytest.raises(KeyError, match="undefined token.*buld_root"):
            resolve_tokens(str(tmp_path), config, {})

    def test_graph_validation_catches_cycle(self, tmp_path: Path):
        """Cycles in config tokens are caught before expansion."""
        config = {"tokens": {"a": "{b}", "b": "{a}"}}
        with pytest.raises(ValueError, match="Circular token reference"):
            resolve_tokens(str(tmp_path), config, {})

    def test_graph_validation_catches_self_reference(self, tmp_path: Path):
        """Self-referencing config token is caught before expansion."""
        config = {"tokens": {"x": "pre{x}post"}}
        with pytest.raises(ValueError, match="references itself"):
            resolve_tokens(str(tmp_path), config, {})

    def test_warning_logged_on_resolution_failure(self, tmp_path: Path, caplog):
        """When resolution fails post-validation, a warning is logged."""
        # Build a chain that passes validation but exceeds MAX_DEPTH
        n = TokenFormatter.MAX_DEPTH + 5
        chain = {f"t{i}": f"{{t{i + 1}}}" for i in range(n)}
        chain[f"t{n}"] = "end"
        config = {"tokens": chain}
        repo_logger = logging.getLogger("repo_tools")
        repo_logger.propagate = True
        try:
            with caplog.at_level(logging.WARNING, logger="repo_tools"):
                resolve_tokens(str(tmp_path), config, {})
            assert any("could not be resolved" in r.message for r in caplog.records)
        finally:
            repo_logger.propagate = False


# ── Path tokens ──────────────────────────────────────────────────────


class TestPathTokens:
    """Tests for path: true token schema."""

    def test_path_true_normalizes_backslashes(self, tmp_path: Path):
        """A path: true token gets posix_path() normalization."""
        config = {"tokens": {
            "my_path": {"path": True, "value": "C:\\Repos\\proj\\_build"},
        }}
        tokens = resolve_tokens(str(tmp_path), config, {})
        assert "\\" not in tokens["my_path"]
        assert "C:/Repos/proj/_build" == tokens["my_path"]

    def test_plain_string_unaffected(self, tmp_path: Path):
        """Plain string tokens are not path-normalized."""
        config = {"tokens": {"plain": "no\\change\\here"}}
        tokens = resolve_tokens(str(tmp_path), config, {})
        assert tokens["plain"] == "no\\change\\here"

    def test_dict_without_path_true(self, tmp_path: Path):
        """A dict token without path: true is treated as plain value."""
        config = {"tokens": {
            "my_var": {"value": "C:\\some\\path"},
        }}
        tokens = resolve_tokens(str(tmp_path), config, {})
        assert tokens["my_var"] == "C:\\some\\path"

    def test_path_token_cross_reference(self, tmp_path: Path):
        """path: true tokens participate in cross-references correctly."""
        config = {"tokens": {
            "root": {"path": True, "value": "C:\\Repos\\proj"},
            "out": "{root}/build",
        }}
        tokens = resolve_tokens(str(tmp_path), config, {})
        assert tokens["out"] == "C:/Repos/proj/build"
