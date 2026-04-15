"""Stdio MCP server exposing dynamic ``repo_*`` tools.

Invoked by Claude Code as::

    python -m repo_tools.agent.mcp.repo_cmd --project-root <path> --config <json> [--extra-tools <json>]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..repo_cmd import build_repo_run_handler, build_repo_run_schema
from ._jsonrpc import make_dispatch, serve_stdio


def main() -> None:
    parser = argparse.ArgumentParser(description="Repo command MCP stdio server")
    parser.add_argument("--project-root", required=True, help="Project root directory")
    parser.add_argument("--config", required=True, help="JSON-encoded config dict")
    parser.add_argument(
        "--extra-tools", default=None,
        help='JSON list of {"name", "description"} dicts for registered tools',
    )
    parser.add_argument(
        "--project-tool-dirs", default=None,
        help="JSON list of directories to prepend to sys.path before "
             "populating the tool registry (enables the project's "
             "format_mcp_output filters to run against subprocess output)",
    )
    args = parser.parse_args()

    root = Path(args.project_root)
    config = json.loads(args.config)
    extra = json.loads(args.extra_tools) if args.extra_tools else None
    project_tool_dirs = (
        json.loads(args.project_tool_dirs) if args.project_tool_dirs else []
    )

    # Inject project-side repo_tools/ portions before populating so the
    # project's BuildTool / TestTool / etc. register and their
    # format_mcp_output filters actually run against subprocess output.
    from ...core import ensure_project_tools_on_path, populate_registry

    if project_tool_dirs:
        ensure_project_tools_on_path(project_tool_dirs)
    populate_registry(config)

    schema = build_repo_run_schema(config, extra=extra)
    name, handler = build_repo_run_handler(config, root, extra=extra)

    dispatch = make_dispatch(
        server_name="repo_cmd",
        version="0.1",
        tools=[schema],
        handlers={name: handler},
    )
    serve_stdio(dispatch, label="repo_cmd_mcp")


if __name__ == "__main__":
    main()
