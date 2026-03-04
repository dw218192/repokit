"""Claude Code PostToolUse hook for the events subscribe tool.

Stops the session (``continue: false``) after a successful subscribe so the
parent event loop can poll for the event and resume later.

If subscribe returned an error, the session continues normally.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    event = json.loads(sys.stdin.read())

    # tool_result.isError is set when validation failed (unknown event, missing param).
    tool_result = event.get("tool_result", {})
    if tool_result.get("isError"):
        return  # Let the session continue — subscribe failed.

    json.dump(
        {"continue": False, "stopReason": "Event subscribed — session yielding to event loop."},
        sys.stdout,
    )
