"""Shared JSON-RPC 2.0 helpers for stdio MCP servers.

All CLI-backend MCP servers share the same protocol boilerplate: initialize,
ping, tools/list, tools/call dispatch, and a stdin read loop.  This module
extracts that into reusable building blocks.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

_PROTOCOL_VERSION = "2024-11-05"


def respond(req_id: Any, result: Any = None, error: Any = None) -> str:
    """Build a JSON-RPC 2.0 response line."""
    msg: dict = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return json.dumps(msg)


def make_dispatch(
    server_name: str,
    version: str,
    tools: list[dict],
    handlers: dict[str, Callable[[dict], dict]],
    *,
    allowed_tools: set[str] | None = None,
) -> Callable[[dict], str | None]:
    """Return a JSON-RPC dispatch function for a stdio MCP server.

    *handlers* maps tool names to callables ``(args) -> {"text": ...}``
    (optionally with ``"isError": True``).

    If *allowed_tools* is set, only those tool names are exposed in
    ``tools/list`` and permitted in ``tools/call``.
    """
    visible_tools = tools
    if allowed_tools is not None:
        visible_tools = [t for t in tools if t["name"] in allowed_tools]

    def dispatch(req: dict) -> str | None:
        req_id = req.get("id")
        method = req.get("method", "")

        if req_id is None:
            return None

        if method == "initialize":
            return respond(req_id, {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": server_name, "version": version},
            })

        if method == "ping":
            return respond(req_id, {})

        if method.startswith("notifications/"):
            return None

        if method == "tools/list":
            return respond(req_id, {"tools": visible_tools})

        if method == "tools/call":
            params = req.get("params", {})
            name = params.get("name", "")
            tool_args = params.get("arguments", {})

            if allowed_tools is not None and name not in allowed_tools:
                outcome = {"isError": True, "text": f"cannot use tool {name!r}"}
            elif name not in handlers:
                outcome = {"isError": True, "text": f"Unknown tool: {name!r}"}
            else:
                outcome = handlers[name](tool_args)

            result = {
                "content": [{"type": "text", "text": outcome["text"]}],
                **({"isError": True} if outcome.get("isError") else {}),
            }
            return respond(req_id, result)

        return respond(req_id, error={
            "code": -32601, "message": f"Method not found: {method}",
        })

    return dispatch


def serve_stdio(
    dispatch_fn: Callable[[dict], str | None],
    *,
    label: str = "mcp",
) -> None:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(f"{label}: malformed JSON-RPC, skipping: {line!r}", file=sys.stderr)
            continue

        try:
            response = dispatch_fn(req)
        except Exception as exc:
            print(f"{label}: dispatch error: {exc}", file=sys.stderr)
            req_id = req.get("id") if isinstance(req, dict) else None
            if req_id is not None:
                response = respond(
                    req_id, error={"code": -32603, "message": f"Internal error: {exc}"},
                )
            else:
                continue

        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()
