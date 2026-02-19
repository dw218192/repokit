"""Tests for CommandRunnerTool base class (repo_tools.command_runner)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from repo_tools.command_runner import CommandRunnerTool


class TestCommandRunnerTool:
    """Unit tests for CommandRunnerTool.execute()."""

    def test_token_expansion(self, make_tool_context):
        """Token placeholders in the command string are resolved before execution."""
        ctx = make_tool_context(
            dimensions={"platform": "linux-x64", "build_type": "Debug"},
        )
        tool = CommandRunnerTool()
        args = {"command": "echo {build_type}"}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            mock_run.assert_called_once()
            resolved_cmd = mock_run.call_args[0][0]
            assert resolved_cmd == ["echo", "Debug"]

    def test_missing_command_exits(self, make_tool_context):
        """Omitting 'command' from args raises SystemExit(1)."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        args = {}

        with pytest.raises(SystemExit) as exc_info:
            tool.execute(ctx, args)
        assert exc_info.value.code == 1

    def test_args_merged_into_tokens(self, make_tool_context):
        """Extra keys in the args dict are available for token expansion."""
        ctx = make_tool_context()
        tool = CommandRunnerTool()
        args = {"command": "deploy --env {target_env}", "target_env": "staging"}

        with patch("repo_tools.command_runner.run_command") as mock_run:
            tool.execute(ctx, args)
            mock_run.assert_called_once()
            resolved_cmd = mock_run.call_args[0][0]
            assert resolved_cmd == ["deploy", "--env", "staging"]
