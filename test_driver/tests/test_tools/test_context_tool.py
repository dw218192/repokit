"""Tests for ContextTool (repo_tools.context)."""

from __future__ import annotations

import json
import logging

from repo_tools.context import ContextTool


class TestContextTool:
    """Unit tests for ContextTool.execute()."""

    def test_json_output(self, make_tool_context, capsys):
        """as_json=True prints valid JSON containing workspace_root."""
        ctx = make_tool_context()
        tool = ContextTool()
        args = {"as_json": True}

        tool.execute(ctx, args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "workspace_root" in data
        assert data["workspace_root"] == ctx.workspace_root.as_posix()

    def test_text_output(self, make_tool_context, capture_logs):
        """as_json=False logs key: value pairs via the logger."""
        ctx = make_tool_context()
        tool = ContextTool()
        args = {"as_json": False}

        tool.execute(ctx, args)

        log_text = capture_logs.getvalue()
        assert "workspace_root" in log_text
