"""Tests for uncovered paths in repo_tools.cli."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from repo_tools.cli import (
    _auto_register_config_tools,
    _build_cli,
    _build_tool_context,
    _get_dimension_tokens,
)


class TestGetDimensionTokens:
    def test_extracts_list_tokens(self):
        config = {"repo": {"tokens": {"platform": ["linux-x64", "windows-x64"], "name": "test"}}}
        result = _get_dimension_tokens(config)
        assert "platform" in result
        assert result["platform"] == ["linux-x64", "windows-x64"]
        assert "name" not in result

    def test_empty_list_skipped(self):
        config = {"repo": {"tokens": {"empty": []}}}
        result = _get_dimension_tokens(config)
        assert result == {}

    def test_no_tokens(self):
        result = _get_dimension_tokens({})
        assert result == {}


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
        config = {"build": {"steps": ["cmake --build {build_dir}"]}}
        result = _auto_register_config_tools(config, set())
        assert len(result) == 1
        assert result[0].name == "build"

    def test_filter_steps_registered(self):
        """Sections with steps@filter keys are eligible."""
        config = {"build": {"steps@windows-x64": ["msbuild"], "steps@linux-x64": ["make"]}}
        result = _auto_register_config_tools(config, set())
        assert len(result) == 1
        assert result[0].name == "build"

    def test_tokens_section_always_skipped(self):
        config = {"tokens": {"steps": ["not-a-tool"]}}
        result = _auto_register_config_tools(config, set())
        assert result == []

    def test_non_dict_section_skipped(self):
        config = {"version": "1.0", "flags": ["a", "b"]}
        result = _auto_register_config_tools(config, set())
        assert result == []

    def test_no_steps_key_skipped(self):
        """A section without steps is silently ignored."""
        config = {"my_section": {"verbose_flag": "-v", "other": "stuff"}}
        result = _auto_register_config_tools(config, set())
        assert result == []

    def test_registered_name_skipped(self):
        config = {"format": {"steps": ["ruff format ."]}}
        result = _auto_register_config_tools(config, {"format"})
        assert result == []

    def test_multiple_tools(self):
        config = {
            "build": {"steps": ["cmake --build ."]},
            "test": {"steps": ["ctest ."]},
            "deploy": {"steps": ["rsync . server:/app"]},
        }
        result = _auto_register_config_tools(config, set())
        names = {t.name for t in result}
        assert names == {"build", "test", "deploy"}

    def test_section_without_steps_not_eligible(self):
        """Extra keys don't matter — only steps presence counts."""
        config = {"build": {"env_script": "setup.sh", "cwd": "/tmp"}}
        result = _auto_register_config_tools(config, set())
        assert result == []

    def test_steps_with_extra_keys_still_eligible(self):
        """Sections with steps plus other keys are still eligible."""
        config = {"build": {"steps": ["make"], "verbose_flag": "-v"}}
        result = _auto_register_config_tools(config, set())
        assert len(result) == 1
        assert result[0].name == "build"


class TestCLICallbackPaths:
    def test_config_dimension_tokens_with_cli_value(self, make_workspace):
        ws = make_workspace(
            config_yaml="""\
            repo:
                tokens:
                    platform: [windows-x64, linux-x64]
                    build_type: [Debug, Release]
            build:
                steps:
                    - "cmake --build {build_dir}"
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
        cli = _build_cli(workspace_root=None)
        result = CliRunner().invoke(cli, ["--workspace-root", str(tmp_path), "--help"])
        assert result.exit_code == 0

    def test_different_workspace_root_reloads_config(self, tmp_path):
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"
        ws1.mkdir()
        ws2.mkdir()
        (ws2 / "config.yaml").write_text("build:\n  steps:\n    - cmake\n", encoding="utf-8")

        cli = _build_cli(workspace_root=str(ws1))
        result = CliRunner().invoke(cli, ["--workspace-root", str(ws2), "--help"])
        assert result.exit_code == 0
