"""Tests for config loading and @filter resolution in repo_tools.core."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from repo_tools.core import _CONFIG_DEFAULTS, _deep_merge, load_config, resolve_filters

_NO_DEFAULTS = patch("repo_tools.core._CONFIG_DEFAULTS", Path("/nonexistent"))


# ── load_config ───────────────────────────────────────────────────────


class TestLoadConfig:
    """Unit tests for load_config() (isolated from framework defaults)."""

    @_NO_DEFAULTS
    def test_load_config_no_file(self, tmp_path: Path):
        """Missing config.yaml returns an empty dict."""
        assert load_config(str(tmp_path)) == {}

    @_NO_DEFAULTS
    def test_load_config_empty(self, tmp_path: Path):
        """An empty (or whitespace-only) config.yaml returns an empty dict."""
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")
        assert load_config(str(tmp_path)) == {}

    @_NO_DEFAULTS
    def test_load_config_valid(self, tmp_path: Path):
        """A well-formed YAML mapping is parsed correctly."""
        yaml_text = "tokens:\n  greeting: hello\nbuild:\n  command: cmake --build .\n"
        (tmp_path / "config.yaml").write_text(yaml_text, encoding="utf-8")
        cfg = load_config(str(tmp_path))

        assert cfg["tokens"]["greeting"] == "hello"
        assert cfg["build"]["command"] == "cmake --build ."

    @_NO_DEFAULTS
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

    def test_plus_extends_list(self):
        base = {"paths": ["a", "b"]}
        overlay = {"paths+": ["c"]}
        assert _deep_merge(base, overlay) == {"paths": ["a", "b", "c"]}

    def test_plus_no_base_creates_list(self):
        assert _deep_merge({}, {"paths+": ["a"]}) == {"paths": ["a"]}

    def test_plus_in_middle_is_literal(self):
        """A '+' not at the end is a normal key, not list extension."""
        assert _deep_merge({}, {"w+ffad": 1}) == {"w+ffad": 1}

    def test_plus_non_list_base_replaced(self):
        """When base value isn't a list, key+ replaces it."""
        assert _deep_merge({"x": "str"}, {"x+": ["a"]}) == {"x": ["a"]}

    def test_plus_value_not_list_is_literal(self):
        """key+ with a non-list value is treated as a literal key."""
        assert _deep_merge({}, {"key+": "not a list"}) == {"key+": "not a list"}

    def test_plus_nested(self):
        """key+ works inside nested dicts."""
        base = {"clean": {"paths": ["_build"]}}
        overlay = {"clean": {"paths+": ["dist"]}}
        assert _deep_merge(base, overlay) == {"clean": {"paths": ["_build", "dist"]}}


# ── config.local.yaml merge ──────────────────────────────────────────


class TestConfigLocalMerge:
    """Tests for config.local.yaml deep-merge in load_config()."""

    @_NO_DEFAULTS
    def test_local_overrides_scalar(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("key: a\n", encoding="utf-8")
        (tmp_path / "config.local.yaml").write_text("key: b\n", encoding="utf-8")
        assert load_config(str(tmp_path)) == {"key": "b"}

    @_NO_DEFAULTS
    def test_local_deep_merges_dicts(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text(
            "build:\n  flags: --std\n  opt: O2\n", encoding="utf-8"
        )
        (tmp_path / "config.local.yaml").write_text(
            "build:\n  opt: O0\n  extra: true\n", encoding="utf-8"
        )
        result = load_config(str(tmp_path))
        assert result == {"build": {"flags": "--std", "opt": "O0", "extra": True}}

    @_NO_DEFAULTS
    def test_local_replaces_lists(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("steps:\n  - a\n", encoding="utf-8")
        (tmp_path / "config.local.yaml").write_text("steps:\n  - b\n", encoding="utf-8")
        assert load_config(str(tmp_path)) == {"steps": ["b"]}

    @_NO_DEFAULTS
    def test_local_adds_new_keys(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("existing: 1\n", encoding="utf-8")
        (tmp_path / "config.local.yaml").write_text("new_key: 2\n", encoding="utf-8")
        assert load_config(str(tmp_path)) == {"existing": 1, "new_key": 2}

    @_NO_DEFAULTS
    def test_local_absent_returns_base(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("key: value\n", encoding="utf-8")
        assert load_config(str(tmp_path)) == {"key": "value"}

    @_NO_DEFAULTS
    def test_local_empty_returns_base(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("key: value\n", encoding="utf-8")
        (tmp_path / "config.local.yaml").write_text("", encoding="utf-8")
        assert load_config(str(tmp_path)) == {"key": "value"}

    @_NO_DEFAULTS
    def test_local_non_dict_raises(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("key: value\n", encoding="utf-8")
        (tmp_path / "config.local.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(TypeError, match="config.local.yaml must contain a top-level mapping"):
            load_config(str(tmp_path))

    @_NO_DEFAULTS
    def test_local_without_base_applies(self, tmp_path: Path):
        """config.local.yaml is loaded even when config.yaml is absent."""
        (tmp_path / "config.local.yaml").write_text("key: value\n", encoding="utf-8")
        assert load_config(str(tmp_path)) == {"key": "value"}


# ── config.defaults.yaml (framework defaults layer) ──────────────────


class TestConfigDefaults:
    """Tests for the framework defaults layer in load_config()."""

    def test_defaults_loaded_without_project_config(self, tmp_path: Path):
        """Framework defaults are returned even with no config.yaml."""
        cfg = load_config(str(tmp_path))
        assert "events" in cfg
        assert "github" in cfg["events"]

    def test_defaults_file_exists(self):
        """config.defaults.yaml ships with the framework."""
        assert _CONFIG_DEFAULTS.exists()

    def test_defaults_file_valid(self):
        """config.defaults.yaml contains a valid YAML dict."""
        data = yaml.safe_load(_CONFIG_DEFAULTS.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_project_extends_defaults(self, tmp_path: Path):
        """Project config adds new keys alongside framework defaults."""
        (tmp_path / "config.yaml").write_text("custom_key: 42\n", encoding="utf-8")
        cfg = load_config(str(tmp_path))
        assert cfg["custom_key"] == 42
        assert "events" in cfg  # defaults still present

    def test_project_overrides_default_event(self, tmp_path: Path):
        """Project config can override a specific built-in event."""
        override = (
            "events:\n"
            "  github:\n"
            "    ci_complete:\n"
            "      doc: Custom CI\n"
            "      params:\n"
            "        run_id: { required: true }\n"
            "      poll: custom-poll\n"
            "      payload: custom-payload\n"
        )
        (tmp_path / "config.yaml").write_text(override, encoding="utf-8")
        cfg = load_config(str(tmp_path))
        assert cfg["events"]["github"]["ci_complete"]["doc"] == "Custom CI"
        # Other built-in events still present via deep merge
        assert "pr_checks_pass" in cfg["events"]["github"]

    def test_local_overrides_default(self, tmp_path: Path):
        """config.local.yaml can override framework defaults."""
        (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
        override = (
            "events:\n"
            "  github:\n"
            "    ci_complete:\n"
            "      doc: Local override\n"
            "      params:\n"
            "        run_id: { required: true }\n"
            "      poll: local-poll\n"
            "      payload: local-payload\n"
        )
        (tmp_path / "config.local.yaml").write_text(override, encoding="utf-8")
        cfg = load_config(str(tmp_path))
        assert cfg["events"]["github"]["ci_complete"]["doc"] == "Local override"

    def test_clean_defaults_loaded(self, tmp_path: Path):
        """Framework defaults include clean paths."""
        cfg = load_config(str(tmp_path))
        assert "clean" in cfg
        assert isinstance(cfg["clean"]["paths"], list)
        assert len(cfg["clean"]["paths"]) > 0

    def test_clean_paths_extended_by_project(self, tmp_path: Path):
        """Project config extends clean defaults via paths+."""
        (tmp_path / "config.yaml").write_text(
            'clean:\n  paths+:\n    - "{workspace_root}/dist"\n',
            encoding="utf-8",
        )
        cfg = load_config(str(tmp_path))
        paths = cfg["clean"]["paths"]
        # Framework defaults still present
        assert any("__pycache__" in p for p in paths)
        # Project extension merged in
        assert "{workspace_root}/dist" in paths

    def test_list_extension_across_layers(self, tmp_path: Path):
        """key+ in project config extends framework default event list."""
        (tmp_path / "config.yaml").write_text(
            "events:\n  github:\n    custom:\n"
            "      doc: Custom event\n      poll: cmd\n      payload: cmd\n"
            "      params: {}\n",
            encoding="utf-8",
        )
        cfg = load_config(str(tmp_path))
        # Built-in events from defaults still present
        assert "ci_complete" in cfg["events"]["github"]
        # Project event merged in
        assert "custom" in cfg["events"]["github"]

    @_NO_DEFAULTS
    def test_no_defaults_file_returns_empty(self, tmp_path: Path):
        """Without defaults file, load_config returns only project config."""
        assert load_config(str(tmp_path)) == {}
