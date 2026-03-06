"""Stdio MCP server exposing dynamic ``repo_*`` tools.

Invoked by Claude Code as::

    python -m repo_tools.agent.mcp.repo_cmd --project-root <path> --config <json>
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
    args = parser.parse_args()

    root = Path(args.project_root)
    config = json.loads(args.config)

    schemas = build_tool_schemas(config)
    handlers = build_tool_handlers(config, root)

    dispatch = make_dispatch(
        server_name="repo_cmd",
        version="0.1",
        tools=schemas,
        handlers=handlers,
    )
    serve_stdio(dispatch, label="repo_cmd_mcp")


if __name__ == "__main__":
    main()
