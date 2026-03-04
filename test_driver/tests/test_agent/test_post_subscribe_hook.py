"""Tests for the post_subscribe PostToolUse hook."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from repo_tools.agent.hooks.post_subscribe import main


def _run_hook(tool_result: dict) -> dict | None:
    """Run the hook with a PostToolUse event payload, return parsed JSON output."""
    event = {"tool_result": tool_result}
    captured = StringIO()
    with patch("sys.stdin", StringIO(json.dumps(event))), \
         patch("sys.stdout", captured):
        main()
    output = captured.getvalue().strip()
    return json.loads(output) if output else None


class TestPostSubscribeHook:
    def test_stops_session_on_success(self):
        """Hook returns continue=false when subscribe succeeded."""
        result = _run_hook({"content": [{"text": "Subscribed to ci.done"}]})

        assert result is not None
        assert result["continue"] is False
        assert "stopReason" in result

    def test_noop_on_error(self):
        """Hook is a no-op when subscribe returned an error."""
        result = _run_hook({"isError": True, "content": [{"text": "Unknown event"}]})

        assert result is None

    def test_noop_on_empty_result(self):
        """Hook is a no-op when tool_result is empty."""
        result = _run_hook({})

        assert result is not None
        assert result["continue"] is False
