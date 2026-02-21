"""Tests for uncovered paths in repo_tools.cli."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from repo_tools.cli import (
    _auto_detect_dimension,
    _auto_register_config_tools,
    _build_cli,
    _build_tool_context,
    _get_dimension_tokens,
)


class TestGetDimensionTokens:
    def test_extracts_list_tokens(self):
        config = {"tokens": {"platform": ["linux-x64", "windows-x64"], "name": "test"}}
        result = _get_dimension_tokens(config)
        assert "platform" in result
        assert result["platform"] == ["linux-x64", "windows-x64"]
        assert "name" not in result

    def test_empty_list_skipped(self):
        config = {"tokens": {"empty": []}}
        result = _get_dimension_tokens(config)
        assert result == {}

    def test_no_tokens(self):
        result = _get_dimension_tokens({})
        assert result == {}


class TestAutoDetectDimension:
    @patch("repo_tools.cli.detect_platform_identifier", return_value="linux-x64")
    def test_platform(self, mock_detect):
        assert _auto_detect_dimension("platform") == "linux-x64"

    def test_build_type(self):
        assert _auto_detect_dimension("build_type") == "Debug"

    def test_unknown(self):
        assert _auto_detect_dimension("unknown_dim") is None


class TestBuildToolContext:
    def test_basic(self):
        ctx_obj = {
            "workspace_root": "/tmp/ws",
            "config": {"my_tool": {"key": "val"}},
            "tokens": {"workspace_root": "/tmp/ws"},
            "dimensions": {"platform": "linux-x64"},
        }
        result = _build_tool_context(ctx_obj, "my_tool")
        assert result.workspace_root == Path("/tmp/ws")
        assert result.tool_config == {"key": "val"}

    def test_non_dict_tool_config(self):
        ctx_obj = {
            "workspace_root": "/tmp/ws",
            "config": {"my_tool": "not_a_dict"},
            "tokens": {},
            "dimensions": {},
        }
        result = _build_tool_context(ctx_obj, "my_tool")
        assert result.tool_config == {}

    def test_missing_tool_config(self):
        ctx_obj = {
            "workspace_root": "/tmp/ws",
            "config": {},
            "tokens": {},
            "dimensions": {},
        }
        result = _build_tool_context(ctx_obj, "nonexistent")
        assert result.tool_config == {}


class TestAutoRegisterConfigTools:
    def test_basic_registration(self):
        config = {"build": {"command": "cmake --build {build_dir}"}}
        result = _auto_register_config_tools(config, set())
        assert len(result) == 1
        assert result[0].name == "build"

    def test_filter_commands_registered(self):
        """Sections with only command@filter keys are eligible."""
        config = {"build": {"command@windows-x64": "msbuild", "command@linux-x64": "make"}}
        result = _auto_register_config_tools(config, set())
        assert len(result) == 1
        assert result[0].name == "build"

    def test_tokens_section_always_skipped(self):
        """The 'tokens' section is never auto-registered even if it has a command key."""
        config = {"tokens": {"command": "not-a-tool"}}
        result = _auto_register_config_tools(config, set())
        assert result == []

    def test_non_dict_section_skipped(self):
        """Scalar and list config sections are not auto-registered."""
        config = {"version": "1.0", "flags": ["a", "b"]}
        result = _auto_register_config_tools(config, set())
        assert result == []

    def test_no_command_key_skipped(self):
        """A section with no command key is silently ignored."""
        config = {"my_section": {"verbose_flag": "-v", "other": "stuff"}}
        result = _auto_register_config_tools(config, set())
        assert result == []

    def test_non_command_key_warns_and_skips(self, capture_logs):
        """A section with both command and non-command keys emits a warning and is skipped."""
        config = {"test": {"command": "pytest", "verbose_flag": "-v"}}
        result = _auto_register_config_tools(config, set())
        assert result == []
        assert "non-command keys" in capture_logs.getvalue()
        assert "verbose_flag" in capture_logs.getvalue()

    def test_registered_name_skipped(self):
        """A config section whose name is already registered is skipped."""
        config = {"format": {"command": "ruff format ."}}
        result = _auto_register_config_tools(config, {"format"})
        assert result == []

    def test_multiple_tools(self):
        """Multiple eligible sections all get registered."""
        config = {
            "build": {"command": "cmake --build ."},
            "test": {"command": "ctest ."},
            "deploy": {"command": "rsync . server:/app"},
        }
        result = _auto_register_config_tools(config, set())
        names = {t.name for t in result}
        assert names == {"build", "test", "deploy"}


class TestCLICallbackPaths:
    def test_config_dimension_tokens_with_cli_value(self, make_workspace):
        """Dimension tokens from config produce CLI flags; passing them updates tokens."""
        ws = make_workspace(
            config_yaml="""\
            tokens:
                platform: [windows-x64, linux-x64]
                build_type: [Debug, Release]
            build:
                command: "cmake --build {build_dir}"
            """
        )
        cli = _build_cli(workspace_root=str(ws))
        result = CliRunner().invoke(
            cli, ["--platform", "linux-x64", "--build-type", "Release", "context", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["platform"] == "linux-x64"
        assert data["build_type"] == "Release"

    def test_workspace_root_none_defaults_to_cwd(self, tmp_path):
        """When _build_cli has no workspace_root, callback falls back to cwd."""
        cli = _build_cli(workspace_root=None)
        result = CliRunner().invoke(cli, ["--workspace-root", str(tmp_path), "--help"])
        assert result.exit_code == 0

    def test_different_workspace_root_reloads_config(self, tmp_path):
        """Passing --workspace-root that differs from _build_cli default triggers config reload."""
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"
        ws1.mkdir()
        ws2.mkdir()
        (ws2 / "config.yaml").write_text("build:\n  command: cmake\n", encoding="utf-8")

        cli = _build_cli(workspace_root=str(ws1))
        result = CliRunner().invoke(cli, ["--workspace-root", str(ws2), "--help"])
        assert result.exit_code == 0
