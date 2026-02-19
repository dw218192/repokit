"""Tests for CleanTool (repo_tools.clean)."""

from __future__ import annotations

from repo_tools.clean import CleanTool


class TestCleanTool:
    """Unit tests for CleanTool.execute()."""

    def test_clean_all_removes_both_roots(self, tmp_path, make_tool_context):
        """clean_all=True removes both build_root and logs_root."""
        build_root = tmp_path / "ws" / "_build"
        logs_root = tmp_path / "ws" / "_logs"
        build_root.mkdir(parents=True)
        logs_root.mkdir(parents=True)
        (build_root / "artifact.o").touch()
        (logs_root / "run.log").touch()

        ctx = make_tool_context(workspace_root=tmp_path / "ws")
        tool = CleanTool()
        args = {
            "clean_all": True,
            "clean_build": False,
            "clean_logs": False,
            "dry_run": False,
        }

        tool.execute(ctx, args)

        assert not build_root.exists()
        assert not logs_root.exists()

    def test_clean_build_removes_build_dir(self, tmp_path, make_tool_context):
        """clean_build=True removes just the build_dir (platform/build_type subdirectory)."""
        ctx = make_tool_context(workspace_root=tmp_path / "ws")
        # Create the build_dir that the tool will resolve from tokens
        from pathlib import Path

        build_dir = Path(ctx.tokens["build_dir"])
        build_dir.mkdir(parents=True)
        (build_dir / "output.bin").touch()

        tool = CleanTool()
        args = {
            "clean_build": True,
            "clean_all": False,
            "clean_logs": False,
            "dry_run": False,
        }

        tool.execute(ctx, args)

        assert not build_dir.exists()

    def test_dry_run_no_delete(self, tmp_path, make_tool_context):
        """dry_run=True reports what would be removed but does not delete."""
        build_root = tmp_path / "ws" / "_build"
        logs_root = tmp_path / "ws" / "_logs"
        build_root.mkdir(parents=True)
        logs_root.mkdir(parents=True)

        ctx = make_tool_context(workspace_root=tmp_path / "ws")
        tool = CleanTool()
        args = {
            "clean_all": True,
            "clean_build": False,
            "clean_logs": False,
            "dry_run": True,
        }

        tool.execute(ctx, args)

        assert build_root.exists()
        assert logs_root.exists()

    def test_nothing_selected(self, make_tool_context):
        """No clean flags set produces no error and no deletions."""
        ctx = make_tool_context()
        tool = CleanTool()
        args = {
            "clean_all": False,
            "clean_build": False,
            "clean_logs": False,
            "dry_run": False,
        }

        # Should complete without error
        tool.execute(ctx, args)
