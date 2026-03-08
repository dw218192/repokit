"""Stdio MCP server exposing a unified ``lint`` tool.

Invoked by Claude Code as::

    python -m repo_tools.agent.mcp.lint [--select CODES] [--ignore CODES]
"""

from __future__ import annotations

import argparse

from ..lint import TOOL_SCHEMA, call_lint

from ._jsonrpc import make_dispatch, serve_stdio


def main() -> None:
    parser = argparse.ArgumentParser(description="Lint MCP stdio server")
    parser.add_argument("--select", default=None, help="Default ruff --select codes")
    parser.add_argument("--ignore", default=None, help="Default ruff --ignore codes")
    parsed = parser.parse_args()

    default_select = parsed.select
    default_ignore = parsed.ignore

    dispatch = make_dispatch(
        server_name="lint",
        version="0.1",
        tools=[TOOL_SCHEMA],
        handlers={
            "lint": lambda args: call_lint(
                args, default_select=default_select, default_ignore=default_ignore,
            ),
        },
    )
    serve_stdio(dispatch, label="lint_mcp")


if __name__ == "__main__":
    main()
