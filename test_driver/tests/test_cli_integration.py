"""Integration tests for the CLI pipeline via Click's CliRunner."""

from __future__ import annotations

import json

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
    for name in ("context", "clean", "build", "test"):
        assert name in result.output


# ── 2. Context displays token names ─────────────────────────────────


def test_context_displays_tokens(make_workspace, capture_logs):
    ws = make_workspace()
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["context"])
    assert result.exit_code == 0
    log_text = capture_logs.getvalue()
    for token in ("workspace_root", "build_type", "platform"):
        assert token in log_text


# ── 3. Context --json with custom token ─────────────────────────────


def test_context_json(make_workspace):
    ws = make_workspace(
        config_yaml="""\
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
    ws = make_workspace()
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["--build-type", "Release", "context", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["build_type"] == "Release"
    assert "Release" in data["build_dir"]


# ── 5. Config dimension tokens produce CLI options ──────────────────


def test_config_dimension_tokens(make_workspace):
    ws = make_workspace(
        config_yaml="""\
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
            command: "cmake --build {build_dir}"
        """
    )
    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["build", "--help"])
    assert result.exit_code == 0


# ── 7. Clean --all --dry-run keeps directories ─────────────────────


def test_clean_dry_run_all(make_workspace, capture_logs):
    ws = make_workspace()
    build_dir = ws / "_build"
    logs_dir = ws / "_logs"
    build_dir.mkdir()
    logs_dir.mkdir()

    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["clean", "--all", "--dry-run"])
    assert result.exit_code == 0
    assert build_dir.exists(), "_build should still exist after dry run"
    assert logs_dir.exists(), "_logs should still exist after dry run"
    assert "Would remove" in capture_logs.getvalue()


# ── 8. Clean --all removes directories ──────────────────────────────


def test_clean_all_removes_dirs(make_workspace):
    ws = make_workspace()
    build_dir = ws / "_build"
    logs_dir = ws / "_logs"
    build_dir.mkdir()
    logs_dir.mkdir()

    cli = _cli_for(ws)
    result = CliRunner().invoke(cli, ["clean", "--all"])
    assert result.exit_code == 0
    assert not build_dir.exists(), "_build should be removed"
    assert not logs_dir.exists(), "_logs should be removed"
