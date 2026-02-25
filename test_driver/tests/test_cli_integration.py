"""Integration tests for the CLI pipeline via Click's CliRunner."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from repo_tools.cli import _build_cli


def _cli_for(ws):
    """Build a CLI rooted at the given workspace path."""
    return _build_cli(workspace_root=str(ws))


# ── 1. Help with no config ──────────────────────────────────────────


def test_help_no_config(make_workspace):
    ws = make_workspace()
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "context" in result.output


# ── 2. Context displays token names ─────────────────────────────────


def test_context_displays_tokens(make_workspace, capture_logs):
    ws = make_workspace()
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["context"])
    assert result.exit_code == 0
    assert "workspace_root" in capture_logs.getvalue()


# ── 3. Context --json with custom token ─────────────────────────────


def test_context_json(make_workspace):
    ws = make_workspace(
        config_yaml="""\
        repo:
            tokens:
                custom_var: hello
        """
    )
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["context", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["custom_var"] == "hello"
    assert "workspace_root" in data


# ── 4. Dimension flags affect tokens ────────────────────────────────


def test_dimension_flags_affect_tokens(make_workspace):
    """Dimensions defined in config tokens produce group-level flags that update tokens."""
    ws = make_workspace(
        config_yaml="""\
        repo:
            tokens:
                build_type: [Debug, Release]
        """
    )
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["--build-type", "Release", "context", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["build_type"] == "Release"


# ── 5. Config dimension tokens produce CLI options ──────────────────


def test_config_dimension_tokens(make_workspace):
    ws = make_workspace(
        config_yaml="""\
        repo:
            tokens:
                platform: [windows-x64, linux-x64]
                build_type: [Debug, Release]
        """
    )
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "--platform" in result.output
    assert "--build-type" in result.output


# ── 6. Tool config merged without crash ─────────────────────────────


def test_tool_config_merged(make_workspace):
    ws = make_workspace(
        config_yaml="""\
        build:
            steps:
                - "cmake --build {build_dir}"
        """
    )
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["build", "--help"])
    assert result.exit_code == 0


# ── 7. Config section with steps auto-generates a tool ──────────────


def test_auto_generated_tool_appears_in_help(make_workspace):
    ws = make_workspace(
        config_yaml="""\
        clean:
            steps:
                - "rm -rf {build_dir}"
        deploy:
            steps:
                - "rsync -av {build_dir}/ server:/app"
        """
    )
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "clean" in result.output
    assert "deploy" in result.output


def test_auto_generated_tool_dry_run(make_workspace, capture_logs):
    ws = make_workspace(
        config_yaml="""\
        repo:
            tokens:
                build_type: [Debug, Release]
        build:
            steps:
                - "cmake --config {build_type}"
        """
    )
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["--build-type", "Release", "build", "--dry-run"])
    assert result.exit_code == 0
    log_text = capture_logs.getvalue()
    assert "Would run" in log_text
    assert "Release" in log_text


# ── 8. Auto-generated tool exposes --dry-run ─────────────────────────


def test_auto_generated_tool_exposes_dry_run(make_workspace):
    """An auto-generated CLI tool created from config exposes the --dry-run flag."""
    ws = make_workspace(
        config_yaml="""\
        build:
            steps:
                - "cmake --build {build_dir}"
        """
    )
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["build", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output


# ── 9. repo section is not auto-registered as a tool ──────────────────


def test_repo_section_not_auto_registered(make_workspace):
    """The 'repo' config section is skipped by auto-registration even if it has steps."""
    ws = make_workspace(
        config_yaml="""\
        repo:
            steps:
                - "echo should not become a tool"
            tokens:
                custom: hello
            features: [conan]
        """
    )
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["repo", "--help"])
    # 'repo' should not be registered as a command — invoke should fail
    assert result.exit_code != 0


# ── 10. Feature-gated tools are hidden when feature not enabled ───────


def test_feature_gated_tool_hidden(make_workspace):
    """A tool with a non-empty feature is hidden when that feature is not in repo.features."""
    _gated_tool_src = """\
from repo_tools.core import RepoTool

class GatedTool(RepoTool):
    name = "gated-cmd"
    help = "A gated tool"
    feature = "conan"

    def execute(self, ctx, args):
        pass
"""
    ws = make_workspace(
        config_yaml="""\
        repo:
            features: [python]
        """,
        project_tool_files={"gated_tool.py": _gated_tool_src},
    )
    cli = _build_cli(workspace_root=str(ws), project_tool_dirs=[str(ws / "tools")])
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "gated-cmd" not in result.output


def test_feature_gated_tool_visible_when_no_features_key(make_workspace):
    """When repo.features is absent, all feature-gated tools are visible."""
    _gated_tool_src = """\
from repo_tools.core import RepoTool

class GatedTool(RepoTool):
    name = "gated-all"
    help = "A gated tool visible when no features key"
    feature = "conan"

    def execute(self, ctx, args):
        pass
"""
    ws = make_workspace(
        project_tool_files={"gated_tool_all.py": _gated_tool_src},
    )
    cli = _build_cli(workspace_root=str(ws), project_tool_dirs=[str(ws / "tools")])
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "gated-all" in result.output


def test_feature_gated_tool_visible_when_enabled(make_workspace):
    """A tool with a feature is visible when that feature is listed in repo.features."""
    _gated_tool_src = """\
from repo_tools.core import RepoTool

class GatedTool2(RepoTool):
    name = "gated-cmd2"
    help = "A gated tool"
    feature = "conan"

    def execute(self, ctx, args):
        pass
"""
    ws = make_workspace(
        config_yaml="""\
        repo:
            features: [conan]
        """,
        project_tool_files={"gated_tool2.py": _gated_tool_src},
    )
    cli = _build_cli(workspace_root=str(ws), project_tool_dirs=[str(ws / "tools")])
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "gated-cmd2" in result.output


# ── 11. __init__.py in project repo_tools/ is rejected ────────────────


def test_init_py_in_project_repo_tools_rejected(make_workspace):
    """An __init__.py in tools/repo_tools/ exits with an error."""
    ws = make_workspace()
    init_file = ws / "tools" / "repo_tools" / "__init__.py"
    init_file.parent.mkdir(parents=True, exist_ok=True)
    init_file.write_text("")

    with pytest.raises(SystemExit):
        _build_cli(
            workspace_root=str(ws),
            project_tool_dirs=[str(ws / "tools")],
        )


# ── 12. Dimension flags after subcommand are honoured ──────────────


def test_dimension_flag_after_subcommand(make_workspace):
    """Dimension flags placed after the subcommand name override group defaults."""
    ws = make_workspace(
        config_yaml="""\
        repo:
            tokens:
                build_type: [Debug, Release]
        """
    )
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["context", "--json", "--build-type", "Release"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["build_type"] == "Release"


def test_dimension_flag_after_subcommand_dry_run(make_workspace, capture_logs):
    """Dimension flags after the subcommand propagate into token resolution."""
    ws = make_workspace(
        config_yaml="""\
        repo:
            tokens:
                build_type: [Debug, Release]
        build:
            steps:
                - "cmake --config {build_type}"
        """
    )
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["build", "--dry-run", "--build-type", "Release"])
    assert result.exit_code == 0
    log_text = capture_logs.getvalue()
    assert "Would run" in log_text
    assert "Release" in log_text
