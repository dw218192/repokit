"""Stdio MCP server exposing dynamic ``repo_*`` tools.

Invoked by Claude Code as::

    python -m repo_tools.agent.mcp.repo_cmd --project-root <path> --config <json> [--extra-tools <json>]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..repo_cmd import build_tool_handlers, build_tool_schemas
from ._jsonrpc import make_dispatch, serve_stdio


def main() -> None:
    parser = argparse.ArgumentParser(description="Repo command MCP stdio server")
    parser.add_argument("--project-root", required=True, help="Project root directory")
    parser.add_argument("--config", required=True, help="JSON-encoded config dict")
    parser.add_argument(
        "--extra-tools", default=None,
        help='JSON list of {"name", "description"} dicts for registered tools',
    )
    args = parser.parse_args()

    root = Path(args.project_root)
    config = json.loads(args.config)
    extra = json.loads(args.extra_tools) if args.extra_tools else None

    schemas = build_tool_schemas(config, extra=extra)
    handlers = build_tool_handlers(config, root, extra=extra)

    dispatch = make_dispatch(
        server_name="repo_cmd",
        version="0.1",
        tools=schemas,
        handlers=handlers,
    )
    serve_stdio(dispatch, label="repo_cmd_mcp")


if __name__ == "__main__":
    main()
