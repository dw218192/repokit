"""Tests for CleanTool (repo_tools.clean)."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from repo_tools.clean import CleanTool
from repo_tools.cli import _build_cli


class TestCleanTool:
    """Unit tests for CleanTool.execute()."""

    def test_default_paths(self, tmp_path, make_tool_context):
        """No config: creates _build/ dir, verifies removed."""
        ws = tmp_path / "ws"
        ws.mkdir()
        build_dir = ws / "_build"
        build_dir.mkdir()
        (build_dir / "artifact.o").write_text("obj")

        ctx = make_tool_context(workspace_root=ws)
        tool = CleanTool()
        args = tool.default_args(ctx.tokens)

        tool.execute(ctx, args)

        assert not build_dir.exists()

    def test_config_paths(self, tmp_path, make_tool_context):
        """Custom paths in tool_config, verifies those removed."""
        ws = tmp_path / "ws"
        ws.mkdir()
        out_dir = ws / "output"
        out_dir.mkdir()
        (out_dir / "data.bin").write_text("binary")

        ctx = make_tool_context(workspace_root=ws)
        tool = CleanTool()
        args = {
            "dry_run": False,
            "paths": [str(out_dir)],
        }

        tool.execute(ctx, args)

        assert not out_dir.exists()

    def test_cross_ref(self, tmp_path, make_tool_context):
        """{cfg:package.output_dir} in paths, verifies resolution."""
        ws = tmp_path / "ws"
        ws.mkdir()
        pkg_dir = ws / "dist"
        pkg_dir.mkdir()
        (pkg_dir / "pkg.tar").write_text("tar")

        config = {"package": {"output_dir": str(pkg_dir)}}
        ctx = make_tool_context(config=config, workspace_root=ws)
        tool = CleanTool()
        args = {
            "dry_run": False,
            "paths": ["{cfg:package.output_dir}"],
        }

        tool.execute(ctx, args)

        assert not pkg_dir.exists()

    def test_glob_patterns(self, tmp_path, make_tool_context):
        """Creates scattered .log files, config **/*.log, verifies all deleted."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "root.log").write_text("log1")
        sub = ws / "subdir"
        sub.mkdir()
        (sub / "nested.log").write_text("log2")
        deep = sub / "deep"
        deep.mkdir()
        (deep / "deep.log").write_text("log3")
        # Also create a non-log file to make sure it's not deleted
        (ws / "keep.txt").write_text("keep")

        ctx = make_tool_context(workspace_root=ws)
        tool = CleanTool()
        args = {
            "dry_run": False,
            "paths": [str(ws / "**" / "*.log")],
        }

        tool.execute(ctx, args)

        assert not (ws / "root.log").exists()
        assert not (sub / "nested.log").exists()
        assert not (deep / "deep.log").exists()
        assert (ws / "keep.txt").exists()

    def test_dry_run(self, tmp_path, make_tool_context, capture_logs):
        """Dirs exist, --dry-run, verifies nothing deleted + log output."""
        ws = tmp_path / "ws"
        ws.mkdir()
        build_dir = ws / "_build"
        build_dir.mkdir()
        (build_dir / "file.o").write_text("obj")

        ctx = make_tool_context(workspace_root=ws)
        tool = CleanTool()
        args = tool.default_args(ctx.tokens)
        args["dry_run"] = True

        tool.execute(ctx, args)

        assert build_dir.exists()
        assert (build_dir / "file.o").exists()
        log_text = capture_logs.getvalue()
        assert "Would remove" in log_text

    def test_protected_skipped(self, tmp_path, make_tool_context, capture_logs):
        """Path resolving to .git, verifies skipped with warning."""
        ws = tmp_path / "ws"
        ws.mkdir()
        git_dir = ws / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main")

        ctx = make_tool_context(workspace_root=ws)
        tool = CleanTool()
        args = {
            "dry_run": False,
            "paths": [str(git_dir)],
        }

        tool.execute(ctx, args)

        assert git_dir.exists()
        log_text = capture_logs.getvalue()
        assert "protected" in log_text.lower()

    def test_outside_workspace(self, tmp_path, make_tool_context, capture_logs):
        """Path outside workspace_root, verifies skipped with warning."""
        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()

        ctx = make_tool_context(workspace_root=ws)
        tool = CleanTool()
        args = {
            "dry_run": False,
            "paths": [str(outside)],
        }

        tool.execute(ctx, args)

        assert outside.exists()
        log_text = capture_logs.getvalue()
        assert "outside workspace" in log_text.lower()

    def test_missing_paths(self, tmp_path, make_tool_context, capture_logs):
        """Nonexistent paths, verifies no error (info log only)."""
        ws = tmp_path / "ws"
        ws.mkdir()

        ctx = make_tool_context(workspace_root=ws)
        tool = CleanTool()
        args = {
            "dry_run": False,
            "paths": [str(ws / "nonexistent_dir")],
        }

        # Should not raise
        tool.execute(ctx, args)

        log_text = capture_logs.getvalue()
        assert "not found" in log_text.lower()

    def test_integration(self, tmp_path):
        """_build_cli + CliRunner, clean --dry-run."""
        ws = tmp_path / "ws"
        ws.mkdir()
        build_dir = ws / "_build"
        build_dir.mkdir()
        (build_dir / "artifact.o").write_text("obj")

        cli = _build_cli(workspace_root=str(ws))
        result = CliRunner().invoke(cli, ["clean", "--dry-run"])

        assert result.exit_code == 0
        assert build_dir.exists(), "dry-run must not delete anything"
