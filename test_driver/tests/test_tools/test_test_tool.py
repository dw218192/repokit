"""Tests for TestTool (repo_tools.test)."""

from __future__ import annotations

from unittest.mock import patch

from repo_tools.test import TestTool


class TestTestTool:
    """Unit tests for TestTool.execute()."""

    def test_verbose_appends_flag(self, make_tool_context):
        """verbose=True appends --output-on-failure to the command."""
        ctx = make_tool_context()
        tool = TestTool()
        args = {"command": "ctest --test-dir /tmp/build", "verbose": True}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            mock_run.assert_called_once()
            resolved_cmd = mock_run.call_args[0][0]
            assert "--output-on-failure" in resolved_cmd

    def test_custom_verbose_flag(self, make_tool_context):
        """A custom verbose_flag overrides the default --output-on-failure."""
        ctx = make_tool_context()
        tool = TestTool()
        args = {
            "command": "ctest --test-dir /tmp/build",
            "verbose": True,
            "verbose_flag": "--verbose",
        }

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            mock_run.assert_called_once()
            resolved_cmd = mock_run.call_args[0][0]
            assert "--verbose" in resolved_cmd
            assert "--output-on-failure" not in resolved_cmd

    def test_no_verbose(self, make_tool_context):
        """Without verbose, command is passed through unchanged."""
        ctx = make_tool_context()
        tool = TestTool()
        original_command = "ctest --test-dir /tmp/build"
        args = {"command": original_command}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            mock_run.assert_called_once()
            resolved_cmd = mock_run.call_args[0][0]
            assert "--output-on-failure" not in resolved_cmd
            assert "--verbose" not in resolved_cmd
