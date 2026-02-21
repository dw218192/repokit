"""Stdio MCP server exposing ``coderabbit_review`` for solo agent sessions.

Claude Code spawns this process on demand when ``type: stdio`` is configured
in ``mcpServers``.  Each request is a newline-delimited JSON-RPC 2.0 message
read from stdin; each response is written as a single JSON line to stdout.
Notifications (no ``id`` field) receive no response.

Invoked by Claude Code as::

    <repo_cmd> python -m repo_tools.agent.hooks.coderabbit_mcp_stdio

Review logic is shared with the HTTP MCP server via
``repo_tools.agent.coderabbit``.
"""

from __future__ import annotations

import json
import sys

from ..coderabbit import call_review

# ── Constants ─────────────────────────────────────────────────────────────────

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "coderabbit"
_SERVER_VERSION = "0.1"

_TOOLS = [
    {
        "name": "coderabbit_review",
        "description": (
            "Run the CodeRabbit CLI to review code changes in a git worktree. "
            "Returns plain-text reviewer feedback. "
            "If the CLI is not installed or not authenticated, returns an error message "
            "instructing you to fall back to manual review."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "worktree_path": {
                    "type": "string",
                    "description": (
                        "Path to the git worktree whose changes should be reviewed. "
                        "Defaults to '.' (the current working directory)."
                    ),
                    "default": ".",
                },
                "type": {
                    "type": "string",
                    "enum": ["committed", "uncommitted", "all"],
                    "default": "committed",
                    "description": "Which changes to review: 'committed' (default), 'uncommitted', or 'all'.",
                },
            },
            "required": [],
        },
    }
]


# ── JSON-RPC dispatch ─────────────────────────────────────────────────────────


def _respond(req_id, result=None, error=None) -> str:
    """Return a JSON-RPC 2.0 response line."""
    msg: dict = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return json.dumps(msg)


def _dispatch(req: dict) -> str | None:
    """Process a single JSON-RPC request. Returns None for notifications."""
    req_id = req.get("id")
    method = req.get("method", "")

    # Notifications have no id — produce no output
    if req_id is None:
        return None

    if method == "initialize":
        return _respond(req_id, {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
        })

    if method == "ping":
        return _respond(req_id, {})

    if method.startswith("notifications/"):
        # Notifications with an id are unusual but we still produce no output
        return None

    if method == "tools/list":
        return _respond(req_id, {"tools": _TOOLS})

    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if name == "coderabbit_review":
            outcome = call_review(tool_args)
        else:
            outcome = {"isError": True, "text": f"Unknown tool: {name!r}"}

        result = {
            "content": [{"type": "text", "text": outcome["text"]}],
            **({"isError": True} if outcome.get("isError") else {}),
        }
        return _respond(req_id, result)

    # Unknown method
    return _respond(req_id, error={"code": -32601, "message": f"Method not found: {method}"})


# ── Main loop ─────────────────────────────────────────────────────────────────


def main() -> None:
    """Read newline-delimited JSON from stdin, write responses to stdout."""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            # Invalid JSON — skip silently (no id to reply to)
            continue

        try:
            response = _dispatch(req)
        except Exception as exc:
            print(f"coderabbit_mcp: dispatch error: {exc}", file=sys.stderr)
            req_id = req.get("id") if isinstance(req, dict) else None
            if req_id is not None:
                response = _respond(req_id, error={"code": -32603, "message": "Internal error"})
            else:
                continue

        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
