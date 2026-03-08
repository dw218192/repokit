"""Stdio MCP server exposing ``coderabbit_review``.

Invoked by Claude Code as::

    python -m repo_tools.agent.mcp.coderabbit
"""

from __future__ import annotations

from ..coderabbit import TOOL_SCHEMA, call_review

from ._jsonrpc import make_dispatch, serve_stdio

_dispatch = make_dispatch(
    server_name="coderabbit",
    version="0.1",
    tools=[TOOL_SCHEMA],
    handlers={"coderabbit_review": lambda args: call_review(args)},
)


def main() -> None:
    serve_stdio(_dispatch, label="coderabbit_mcp")


if __name__ == "__main__":
    main()
