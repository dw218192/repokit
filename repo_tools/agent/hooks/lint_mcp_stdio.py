"""Stdio MCP server exposing a unified ``lint`` tool for agent sessions.

Claude Code spawns this process on demand when ``type: stdio`` is configured
in ``mcpServers``.  Each request is a newline-delimited JSON-RPC 2.0 message
read from stdin; each response is written as a single JSON line to stdout.
Notifications (no ``id`` field) receive no response.

Invoked by Claude Code as::

    <python> -m repo_tools.agent.hooks.lint_mcp_stdio [--select CODES] [--ignore CODES]

Lint logic is shared via ``repo_tools.agent.lint``.
"""

from __future__ import annotations

import argparse
import json
import sys

from ..lint import call_lint

# ── Constants ─────────────────────────────────────────────────────────────────

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "lint"
_SERVER_VERSION = "0.1"

_TOOLS = [
    {
        "name": "lint",
        "description": (
            "Run static analysis on a file or directory. "
            "Automatically detects language and runs the "
            "appropriate linter (ruff for Python, clang-tidy "
            "for C/C++). Returns plain-text diagnostics."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File or directory to lint. "
                        "Defaults to '.'."
                    ),
                    "default": ".",
                },
            },
            "required": [],
        },
    },
]

# ── Module-level defaults (set by CLI args) ──────────────────────────────────

_default_select: str | None = None
_default_ignore: str | None = None


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
        return None

    if method == "tools/list":
        return _respond(req_id, {"tools": _TOOLS})

    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if name == "lint":
            outcome = call_lint(
                tool_args,
                default_select=_default_select,
                default_ignore=_default_ignore,
            )
        else:
            outcome = {"isError": True, "text": f"Unknown tool: {name!r}"}

        result = {
            "content": [{"type": "text", "text": outcome["text"]}],
            **({"isError": True} if outcome.get("isError") else {}),
        }
        return _respond(req_id, result)

    # Unknown method
    return _respond(
        req_id,
        error={"code": -32601, "message": f"Method not found: {method}"},
    )


# ── Main loop ─────────────────────────────────────────────────────────────────


def main() -> None:
    """Read newline-delimited JSON from stdin, write responses to stdout."""
    global _default_select, _default_ignore

    parser = argparse.ArgumentParser(description="Lint MCP stdio server")
    parser.add_argument("--select", default=None, help="Default ruff --select codes")
    parser.add_argument("--ignore", default=None, help="Default ruff --ignore codes")
    parsed = parser.parse_args()
    _default_select = parsed.select
    _default_ignore = parsed.ignore

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        try:
            response = _dispatch(req)
        except Exception as exc:
            print(
                f"lint_mcp: dispatch error: {exc}",
                file=sys.stderr,
            )
            req_id = req.get("id") if isinstance(req, dict) else None
            if req_id is not None:
                response = _respond(
                    req_id,
                    error={"code": -32603, "message": "Internal error"},
                )
            else:
                continue

        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
