"""Stdio MCP server for agent dispatching.

Invoked by Claude Code as::

    python -m repo_tools.agent.mcp.dispatch --project-root <path>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..dispatch import TOOL_SCHEMA, call_dispatch
from ._jsonrpc import make_dispatch, serve_stdio


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent dispatch MCP stdio server")
    parser.add_argument("--project-root", required=True, help="Project root directory")
    args = parser.parse_args()

    root = Path(args.project_root)

    dispatch = make_dispatch(
        server_name="dispatch",
        version="0.1",
        tools=[TOOL_SCHEMA],
        handlers={
            "dispatch_agent": lambda tool_args: call_dispatch(tool_args, workspace_root=root),
        },
    )
    serve_stdio(dispatch, label="dispatch_mcp")


if __name__ == "__main__":
    main()
