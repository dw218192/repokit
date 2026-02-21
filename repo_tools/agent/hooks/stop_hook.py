"""Claude Code Stop hook — signals the MCP server when an agent goes idle.

Reads the Stop event from stdin (unused), reads WEZTERM_PANE from the
environment to identify the pane, then POSTs to ``/idle`` on the local
MCP server so the watchdog can track idle duration.

Usage (in Claude Code settings)::

    {"hooks": {"Stop": [{"hooks": [{"type": "command",
        "command": "./repo python -m repo_tools.agent.hooks.stop_hook --port <N>"}]}]}}

Exit codes:
    0 — always (errors are silently ignored to avoid breaking Claude Code)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal agent idle state to MCP server.")
    parser.add_argument("--port", required=True, type=int, help="MCP server port")
    args = parser.parse_args()

    # Consume stdin (Stop event payload — not used but must be read)
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, EOFError):
        pass

    pane_id_str = os.environ.get("WEZTERM_PANE", "")
    if not pane_id_str:
        return  # Not running inside WezTerm — nothing to signal

    try:
        pane_id = int(pane_id_str)
    except ValueError:
        return

    data = json.dumps({"pane_id": pane_id}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{args.port}/idle",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass  # MCP server not running (solo mode, tests, etc.) — ignore


if __name__ == "__main__":
    main()
