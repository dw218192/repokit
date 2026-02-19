"""Tests for BuildTool (repo_tools.build)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from repo_tools.build import BuildTool


class TestBuildTool:
    """Unit tests for BuildTool.execute()."""

    def test_dispatches_expanded_command(self, make_tool_context):
        """A configured command is token-expanded and forwarded to run_command."""
        ctx = make_tool_context(
            dimensions={"platform": "linux-x64", "build_type": "Release"},
        )
        tool = BuildTool()
        args = {"command": "cmake --build {build_dir} --config {build_type}"}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            mock_run.assert_called_once()
            resolved_cmd = mock_run.call_args[0][0]
            assert "--config" in resolved_cmd
            assert "Release" in resolved_cmd
            # build_dir should have been expanded to a real path
            assert any("_build" in part for part in resolved_cmd)

    def test_no_command_exits(self, make_tool_context):
        """Missing command in args raises SystemExit(1)."""
        ctx = make_tool_context()
        tool = BuildTool()
        args = {}

        with pytest.raises(SystemExit) as exc_info:
            tool.execute(ctx, args)
        assert exc_info.value.code == 1
