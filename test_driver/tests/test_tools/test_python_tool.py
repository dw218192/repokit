"""Tests for PythonTool (repo_tools.python)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from repo_tools.python import PythonTool


class TestPythonTool:
    """Unit tests for PythonTool.execute()."""

    def test_passes_passthrough_args(self, make_tool_context):
        """passthrough_args are forwarded to subprocess.call after sys.executable."""
        import sys

        ctx = make_tool_context(passthrough_args=["script.py", "--flag"])
        tool = PythonTool()
        args = {}

        with patch("repo_tools.python.subprocess.call", return_value=0) as mock_call:
            with pytest.raises(SystemExit) as exc_info:
                tool.execute(ctx, args)

            mock_call.assert_called_once_with([sys.executable, "script.py", "--flag"])
            assert exc_info.value.code == 0

    def test_exit_code_propagated(self, make_tool_context):
        """The exit code from subprocess.call is propagated via SystemExit."""
        ctx = make_tool_context(passthrough_args=["failing_script.py"])
        tool = PythonTool()
        args = {}

        with patch("repo_tools.python.subprocess.call", return_value=42):
            with pytest.raises(SystemExit) as exc_info:
                tool.execute(ctx, args)
            assert exc_info.value.code == 42
