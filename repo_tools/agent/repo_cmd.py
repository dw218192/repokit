"""Dynamic repo command MCP tools.

Discovers configured ``./repo`` subcommands and exposes each as an MCP tool
so agents can invoke them without going through Bash (and hitting allowlist
issues).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

_SKIP_SECTIONS = {"repo", "tokens", "agent", "clean"}
_SKIP_REGISTERED = {"agent"}


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


def _discover_registered_tools() -> list[dict[str, str]]:
    """Return registered ``RepoTool`` instances as command descriptors.

    Reads ``_TOOL_REGISTRY`` (populated by the CLI bootstrap) and returns
    the same ``{"name", "description"}`` format as ``_discover_repo_commands``.
    The ``agent`` tool is excluded to prevent recursion.
    """
    from ..core import _TOOL_REGISTRY

    return [
        {"name": t.name, "description": t.help or f"Run ./repo {t.name}"}
        for t in _TOOL_REGISTRY.values()
        if t.name not in _SKIP_REGISTERED
    ]


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
    env = {**os.environ, "REPOKIT_LOG_JSON": "1"}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
            timeout=300, cwd=str(workspace_root),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "isError": True,
            "text": f"repo {subcommand}: timed out after 300s",
            "stdout": "",
            "stderr": "",
            "returncode": -1,
            "records": [],
        }
    output = (proc.stdout or "") + (proc.stderr or "")
    records = _parse_records(proc.stdout or "", proc.stderr or "")
    if proc.returncode != 0:
        return {
            "isError": True,
            "text": output.strip() or f"exit code {proc.returncode}",
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "returncode": proc.returncode,
            "records": records,
        }
    return {
        "text": output.strip() or f"repo {subcommand} completed.",
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "returncode": proc.returncode,
        "records": records,
    }


def _parse_records(stdout: str, stderr: str) -> list:
    """Parse subprocess output into :class:`~repo_tools.core.McpLogRecord` list."""
    from ..core import McpLogRecord

    records: list[McpLogRecord] = []
    # stderr contains JSON log records (one per line)
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if isinstance(rec, dict) and "level" in rec and "message" in rec:
                records.append(McpLogRecord(rec["level"], rec["message"]))
                continue
        except (json.JSONDecodeError, ValueError):
            pass
        records.append(McpLogRecord("raw", line))
    # stdout contains subprocess/command output
    for line in stdout.splitlines():
        stripped = line.rstrip()
        if stripped:
            records.append(McpLogRecord("output", stripped))
    return records


def _apply_output_filter(subcommand: str, result: dict[str, Any]) -> dict[str, Any]:
    """Apply RepoTool.format_mcp_output if the tool is in the registry."""
    if result.get("isError"):
        return result
    from ..core import get_tool

    tool = get_tool(subcommand)
    if tool is None:
        return result
    filtered = tool.format_mcp_output(result.get("records", []), result["returncode"])
    if filtered is not None:
        return {**result, "text": filtered}
    return result


def build_repo_run_schema(
    config: dict[str, Any],
    extra: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a single ``repo_run`` MCP tool schema with a ``command`` enum."""
    all_cmds = _merge_commands(_discover_repo_commands(config), extra)
    cmd_lines = "\n".join(f"- {c['name']}: {c['description']}" for c in all_cmds)
    return {
        "name": "repo_run",
        "description": f"Run a repo command.\n\nAvailable commands:\n{cmd_lines}",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "enum": [c["name"] for c in all_cmds],
                    "description": "Command to run",
                },
                "extra_args": {
                    "type": "string",
                    "default": "",
                    "description": "Additional CLI arguments",
                },
            },
            "required": ["command"],
        },
    }


def build_repo_run_handler(
    config: dict[str, Any],
    workspace_root: Path,
    extra: list[dict[str, str]] | None = None,
) -> tuple[str, Any]:
    """Build a single ``repo_run`` handler that dispatches by command name."""
    all_cmds = _merge_commands(_discover_repo_commands(config), extra)
    known = {c["name"] for c in all_cmds}

    def handler(args: dict[str, Any]) -> dict[str, Any]:
        command = args.get("command", "")
        if command not in known:
            return {"isError": True, "text": f"Unknown command: {command!r}"}
        result = call_repo_run(command, args, workspace_root=workspace_root)
        return _apply_output_filter(command, result)

    return ("repo_run", handler)


def _merge_commands(
    config_cmds: list[dict[str, str]],
    extra: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    """Merge config-discovered commands with extra tool descriptors, dedup by name."""
    if not extra:
        return config_cmds
    seen = {c["name"] for c in config_cmds}
    merged = list(config_cmds)
    for cmd in extra:
        if cmd["name"] not in seen:
            merged.append(cmd)
            seen.add(cmd["name"])
    return merged
