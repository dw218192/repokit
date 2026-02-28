"""Tests for config loading and @filter resolution in repo_tools.core."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_tools.core import _deep_merge, load_config, resolve_filters


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


# ── _deep_merge ──────────────────────────────────────────────────────


class TestDeepMerge:
    """Unit tests for the _deep_merge() helper."""

    def test_empty_overlay(self):
        base = {"a": 1, "b": {"c": 2}}
        assert _deep_merge(base, {}) == base

    def test_empty_base(self):
        overlay = {"x": 10, "y": {"z": 20}}
        assert _deep_merge({}, overlay) == overlay

    def test_nested_merge(self):
        base = {"a": {"b": 1, "c": 2}, "d": 3}
        overlay = {"a": {"c": 99, "e": 5}}
        result = _deep_merge(base, overlay)
        assert result == {"a": {"b": 1, "c": 99, "e": 5}, "d": 3}

    def test_list_replaced(self):
        base = {"steps": ["a", "b"]}
        overlay = {"steps": ["c"]}
        assert _deep_merge(base, overlay) == {"steps": ["c"]}

    def test_mixed_types(self):
        # dict in base replaced by scalar in overlay
        assert _deep_merge({"k": {"nested": 1}}, {"k": "flat"}) == {"k": "flat"}
        # scalar in base replaced by dict in overlay
        assert _deep_merge({"k": "flat"}, {"k": {"nested": 1}}) == {"k": {"nested": 1}}


# ── config.local.yaml merge ──────────────────────────────────────────


class TestConfigLocalMerge:
    """Tests for config.local.yaml deep-merge in load_config()."""

    def test_local_overrides_scalar(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("key: a\n", encoding="utf-8")
        (tmp_path / "config.local.yaml").write_text("key: b\n", encoding="utf-8")
        assert load_config(str(tmp_path)) == {"key": "b"}

    def test_local_deep_merges_dicts(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text(
            "build:\n  flags: --std\n  opt: O2\n", encoding="utf-8"
        )
        (tmp_path / "config.local.yaml").write_text(
            "build:\n  opt: O0\n  extra: true\n", encoding="utf-8"
        )
        result = load_config(str(tmp_path))
        assert result == {"build": {"flags": "--std", "opt": "O0", "extra": True}}

    def test_local_replaces_lists(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("steps:\n  - a\n", encoding="utf-8")
        (tmp_path / "config.local.yaml").write_text("steps:\n  - b\n", encoding="utf-8")
        assert load_config(str(tmp_path)) == {"steps": ["b"]}

    def test_local_adds_new_keys(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("existing: 1\n", encoding="utf-8")
        (tmp_path / "config.local.yaml").write_text("new_key: 2\n", encoding="utf-8")
        assert load_config(str(tmp_path)) == {"existing": 1, "new_key": 2}

    def test_local_absent_returns_base(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("key: value\n", encoding="utf-8")
        assert load_config(str(tmp_path)) == {"key": "value"}

    def test_local_empty_returns_base(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("key: value\n", encoding="utf-8")
        (tmp_path / "config.local.yaml").write_text("", encoding="utf-8")
        assert load_config(str(tmp_path)) == {"key": "value"}

    def test_local_non_dict_raises(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("key: value\n", encoding="utf-8")
        (tmp_path / "config.local.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(TypeError, match="config.local.yaml must contain a top-level mapping"):
            load_config(str(tmp_path))

    def test_local_without_base_ignored(self, tmp_path: Path):
        (tmp_path / "config.local.yaml").write_text("key: value\n", encoding="utf-8")
        assert load_config(str(tmp_path)) == {}
