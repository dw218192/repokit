"""Stdio MCP server for ticket CRUD.

Invoked by Claude Code as::

    python -m repo_tools.agent.mcp.tickets --project-root <path> [--role ROLE]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..tickets import TOOL_HANDLERS, TOOL_SCHEMAS, _ROLE_ALLOWED_TOOLS

from ._jsonrpc import make_dispatch, serve_stdio


def main() -> None:
    parser = argparse.ArgumentParser(description="Ticket MCP stdio server")
    parser.add_argument("--project-root", required=True, help="Project root directory")
    parser.add_argument(
        "--role", default=None,
        choices=["orchestrator", "worker", "reviewer"],
        help="Agent role for access control",
    )
    args = parser.parse_args()
    root = Path(args.project_root)
    role = args.role

    # Wrap shared handlers to inject root and role via closure.
    handlers = {
        name: (lambda h: lambda tool_args: h(root, tool_args, role=role))(handler)
        for name, handler in TOOL_HANDLERS.items()
    }

    allowed = _ROLE_ALLOWED_TOOLS.get(role) if role else None

    dispatch = make_dispatch(
        server_name="tickets",
        version="0.1",
        tools=TOOL_SCHEMAS,
        handlers=handlers,
        allowed_tools=allowed,
    )
    serve_stdio(dispatch, label="ticket_mcp")


if __name__ == "__main__":
    main()
