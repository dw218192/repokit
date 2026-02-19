"""Tests for the token system in repo_tools.core."""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

from repo_tools.core import TokenFormatter, resolve_tokens


# ── TokenFormatter.resolve ────────────────────────────────────────────


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


# ── resolve_tokens ────────────────────────────────────────────────────


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
        """build_root, logs_root, and build_dir live under the workspace root."""
        ws = tmp_path.as_posix()
        tokens = resolve_tokens(str(tmp_path), {}, {})

        assert tokens["workspace_root"] == ws
        assert tokens["build_root"].startswith(ws)
        assert tokens["logs_root"].startswith(ws)
        assert tokens["build_dir"].startswith(tokens["build_root"])

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
        """Dimension values appear in the token dict and influence build_dir."""
        dims = {"platform": "linux-arm64", "build_type": "Release"}
        tokens = resolve_tokens(str(tmp_path), {}, dims)

        assert tokens["platform"] == "linux-arm64"
        assert tokens["build_type"] == "Release"
        # build_dir should incorporate both dimension values
        assert "linux-arm64" in tokens["build_dir"]
        assert "Release" in tokens["build_dir"]
