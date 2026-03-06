"""Dynamic repo command MCP tools.

Discovers configured ``./repo`` subcommands and exposes each as an MCP tool
so agents can invoke them without going through Bash (and hitting allowlist
issues).
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

_SKIP_SECTIONS = {"repo", "tokens", "agent", "clean"}


def _discover_repo_commands(config: dict[str, Any]) -> list[dict[str, str]]:
    """Scan *config* for sections with ``steps`` keys.

    Returns a list of ``{"name": ..., "description": ...}`` dicts, one per
    discovered command.  Sections in ``_SKIP_SECTIONS`` are ignored.
    """
    commands: list[dict[str, str]] = []
    for section, value in config.items():
        if section in _SKIP_SECTIONS or not isinstance(value, dict):
            continue
        has_steps = any(
            k == "steps" or k.startswith("steps@")
            for k in value
        )
        if has_steps:
            commands.append({"name": section, "description": f"Run ./repo {section}"})
    return commands


def call_repo_run(
    subcommand: str,
    args: dict[str, Any],
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    """Execute ``./repo <subcommand>`` via subprocess.

    Returns ``{"text": ...}`` on success or ``{"isError": True, "text": ...}``
    on failure.
    """
    extra = (args.get("extra_args") or "").strip()
    cmd = [
        sys.executable, "-m", "repo_tools.cli",
        "--workspace-root", str(workspace_root),
        subcommand,
    ]
    if extra:
        cmd.extend(shlex.split(extra))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=300, cwd=str(workspace_root),
        )
    except subprocess.TimeoutExpired:
        return {"isError": True, "text": f"repo {subcommand}: timed out after 300s"}
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return {"isError": True, "text": output.strip() or f"exit code {proc.returncode}"}
    return {"text": output.strip() or f"repo {subcommand} completed."}


def build_tool_schemas(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build MCP tool schemas for all discovered repo commands."""
    schemas: list[dict[str, Any]] = []
    for cmd in _discover_repo_commands(config):
        schemas.append({
            "name": f"repo_{cmd['name']}",
            "description": cmd["description"],
            "inputSchema": {
                "type": "object",
                "properties": {
                    "extra_args": {
                        "type": "string",
                        "description": "Additional CLI arguments",
                        "default": "",
                    },
                },
            },
        })
    return schemas


def build_tool_handlers(
    config: dict[str, Any],
    workspace_root: Path,
) -> dict[str, Any]:
    """Build MCP tool handlers for all discovered repo commands."""
    handlers: dict[str, Any] = {}
    for cmd in _discover_repo_commands(config):
        name = f"repo_{cmd['name']}"

        def _mk(cmd_name: str):
            def handler(args: dict[str, Any]) -> dict[str, Any]:
                return call_repo_run(cmd_name, args, workspace_root=workspace_root)
            return handler

        handlers[name] = _mk(cmd["name"])
    return handlers
