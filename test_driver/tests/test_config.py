"""Tests for config loading and @filter resolution in repo_tools.core."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_tools.core import load_config, resolve_filters


# ── load_config ───────────────────────────────────────────────────────


class TestLoadConfig:
    """Unit tests for load_config()."""

    def test_load_config_no_file(self, tmp_path: Path):
        """Missing config.yaml returns an empty dict."""
        assert load_config(str(tmp_path)) == {}

    def test_load_config_empty(self, tmp_path: Path):
        """An empty (or whitespace-only) config.yaml returns an empty dict."""
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")
        assert load_config(str(tmp_path)) == {}

    def test_load_config_valid(self, tmp_path: Path):
        """A well-formed YAML mapping is parsed correctly."""
        yaml_text = "tokens:\n  greeting: hello\nbuild:\n  command: cmake --build .\n"
        (tmp_path / "config.yaml").write_text(yaml_text, encoding="utf-8")
        cfg = load_config(str(tmp_path))

        assert cfg["tokens"]["greeting"] == "hello"
        assert cfg["build"]["command"] == "cmake --build ."

    def test_load_config_non_dict(self, tmp_path: Path):
        """A YAML file whose top-level value is not a dict raises TypeError."""
        (tmp_path / "config.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(TypeError, match="top-level mapping"):
            load_config(str(tmp_path))


# ── resolve_filters ───────────────────────────────────────────────────


class TestResolveFilters:
    """Unit tests for resolve_filters() and the underlying _match_filter logic."""

    # Dimension values used by most filter tests
    _dims = {"platform": "windows-x64", "build_type": "Debug"}

    def test_filter_basic_match(self):
        """A key@value entry is selected when the dimension matches."""
        config = {
            "command": "default-cmd",
            "command@windows-x64": "win-cmd",
        }
        result = resolve_filters(config, self._dims)
        assert result["command"] == "win-cmd"

    def test_filter_negation(self):
        """A key@!value entry is selected when the dimension does NOT match."""
        dims = {"platform": "linux-x64", "build_type": "Release"}
        config = {
            "command": "default-cmd",
            "command@!windows-x64": "not-win-cmd",
        }
        result = resolve_filters(config, dims)
        assert result["command"] == "not-win-cmd"

    def test_filter_compound(self):
        """A compound filter key@val1,val2 matches when all conditions hold."""
        config = {
            "command": "default-cmd",
            "command@windows-x64,Debug": "win-debug-cmd",
        }
        result = resolve_filters(config, self._dims)
        assert result["command"] == "win-debug-cmd"

    def test_filter_specificity(self):
        """A more-specific filter (more conditions) wins over a less-specific one."""
        config = {
            "command": "default-cmd",
            "command@windows-x64": "win-cmd",
            "command@windows-x64,Debug": "win-debug-cmd",
        }
        result = resolve_filters(config, self._dims)
        assert result["command"] == "win-debug-cmd"

    def test_filter_base_fallback(self):
        """When no filter matches, the base key value is used."""
        dims = {"platform": "linux-x64", "build_type": "Release"}
        config = {
            "command": "fallback-cmd",
            "command@windows-x64": "win-cmd",
        }
        result = resolve_filters(config, dims)
        assert result["command"] == "fallback-cmd"

    def test_filter_nested(self):
        """Filters inside nested dicts are resolved recursively."""
        config = {
            "build": {
                "flags": "--standard",
                "flags@windows-x64": "--windows-optimized",
                "inner": {
                    "tool": "make",
                    "tool@windows-x64": "nmake",
                },
            },
        }
        result = resolve_filters(config, self._dims)
        assert result["build"]["flags"] == "--windows-optimized"
        assert result["build"]["inner"]["tool"] == "nmake"
