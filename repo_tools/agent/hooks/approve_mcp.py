"""PermissionRequest hook that auto-approves MCP tool calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import write_log


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-approve MCP tool permission requests.")
    parser.add_argument("--debug-log", default=None, help="Append hook decisions to this file")
    args = parser.parse_args()

    log_path = Path(args.debug_log) if args.debug_log else None

    event = json.load(sys.stdin)
    tool_name = event.get("tool_name", "")

    if log_path:
        write_log(log_path, tool_name or "mcp_tool", "allow", "auto-approved MCP tool")

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        },
        sys.stdout,
    )
