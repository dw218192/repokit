"""Dispatch agent MCP tool.

Exposes worker/reviewer dispatching as an MCP tool so the orchestrator can
invoke it directly instead of going through Bash.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

TOOL_SCHEMA: dict[str, Any] = {
    "name": "dispatch_agent",
    "description": (
        "Dispatch a worker or reviewer agent for a ticket. "
        "The agent runs in a worktree and updates the ticket on completion. "
        "Returns the agent's structured output."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "string",
                "description": "Ticket identifier (e.g. 'add-auth-hook').",
            },
            "role": {
                "type": "string",
                "enum": ["worker", "reviewer"],
                "description": "Agent role to dispatch.",
            },
            "branch": {
                "type": "string",
                "description": (
                    "Base branch or ref for the worktree (default: HEAD). "
                    "Only used when creating a new worktree branch."
                ),
            },
            "project_dir": {
                "type": "string",
                "description": (
                    "Override workspace root for the dispatched agent. "
                    "Allows dispatching into a different project directory."
                ),
            },
            "timeout": {
                "type": "number",
                "description": (
                    "Timeout in seconds for the agent subprocess. "
                    "Defaults to no timeout (agent runs until completion)."
                ),
            },
        },
        "required": ["ticket_id", "role"],
    },
}


def call_dispatch(
    args: dict[str, Any],
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    """Dispatch a headless agent via subprocess.

    Returns ``{"text": ...}`` on success or ``{"isError": True, "text": ...}``
    on failure.
    """
    role = (args.get("role") or "").strip()
    ticket_id = (args.get("ticket_id") or "").strip()
    branch = (args.get("branch") or "").strip() or None
    project_dir = (args.get("project_dir") or "").strip() or None
    timeout = args.get("timeout")
    if timeout is not None:
        timeout = float(timeout)

    if role not in ("worker", "reviewer"):
        return {"isError": True, "text": f"Invalid role: {role!r}. Must be 'worker' or 'reviewer'."}
    if not ticket_id:
        return {"isError": True, "text": "ticket_id is required."}

    effective_root = Path(project_dir) if project_dir else workspace_root

    cmd = [
        sys.executable, "-m", "repo_tools.cli",
        "--workspace-root", str(effective_root),
        "agent", "--role", role, "--ticket", ticket_id,
    ]
    if branch:
        cmd.extend(["--branch", branch])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"isError": True, "text": f"Agent timed out after {timeout}s."}
    except OSError as exc:
        return {"isError": True, "text": f"Failed to launch agent: {exc}"}

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if proc.returncode != 0:
        parts = [p for p in (stderr, stdout) if p]
        return {
            "isError": True,
            "text": "\n".join(parts) or f"Agent exited with code {proc.returncode}",
        }

    return {"text": stdout or "Dispatch completed."}
