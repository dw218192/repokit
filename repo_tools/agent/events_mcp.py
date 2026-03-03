"""Stdio MCP server for event subscription.

Provides tools for listing available events and subscribing to them.
Subscriptions are written as signal files that the event loop runner picks up.

Invoked by Claude Code as::

    python -m repo_tools.agent.events_mcp --project-root <path> --signal-file <path>

Protocol: newline-delimited JSON-RPC 2.0 over stdin/stdout (same as
``lint_mcp_stdio`` and ``ticket_mcp``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from .events import EventDef, Subscription, load_events, write_signal

# ── Constants ─────────────────────────────────────────────────────────────────

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "events"
_SERVER_VERSION = "0.1"

_TOOLS = [
    {
        "name": "list_events",
        "description": (
            "List available events. Optionally filter by group."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": "Optional group name to filter by.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "subscribe",
        "description": (
            "Subscribe to an event. Validates event_type and required params, "
            "then writes a signal file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "description": "Dotted event name (e.g. 'repo.push').",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters for the subscription.",
                },
            },
            "required": ["event_type"],
        },
    },
]

# ── Module-level state (set by CLI args / main()) ────────────────────────────

_events: dict[str, EventDef] = {}
_signal_file: Path = Path("signal.json")


# ── Tool implementations ─────────────────────────────────────────────────────


def _format_param(name: str, spec: Any) -> str:
    """Format a single parameter for display."""
    if isinstance(spec, dict):
        if spec.get("required", False):
            return f"{name} (required)"
        default = spec.get("default")
        if default is not None:
            return f"{name} (optional, default: {default!r})"
        return f"{name} (optional)"
    return name


def _tool_list_events(args: dict[str, Any]) -> dict[str, Any]:
    """List available events, optionally filtered by group."""
    group_filter = (args.get("group") or "").strip() or None

    # Filter events
    if group_filter:
        filtered = {k: v for k, v in _events.items() if v.group == group_filter}
        if not filtered:
            return {"text": f"No events found in group {group_filter!r}."}
    else:
        filtered = _events

    if not filtered:
        return {"text": "No events defined."}

    # Group events for display
    groups: dict[str, list[EventDef]] = {}
    for ev in filtered.values():
        groups.setdefault(ev.group, []).append(ev)

    lines: list[str] = []
    for gname in sorted(groups):
        lines.append(f"[{gname}]")
        for ev in sorted(groups[gname], key=lambda e: e.name):
            dotted = f"{ev.group}.{ev.name}"
            lines.append(f"  {dotted} \u2014 {ev.doc}")
            if ev.params:
                param_strs = [_format_param(p, s) for p, s in ev.params.items()]
                lines.append(f"    params: {', '.join(param_strs)}")
        lines.append("")

    return {"text": "\n".join(lines).rstrip()}


def _tool_subscribe(args: dict[str, Any]) -> dict[str, Any]:
    """Subscribe to an event after validation."""
    event_type = (args.get("event_type") or "").strip()
    params = args.get("params") or {}

    if not event_type:
        return {"isError": True, "text": "event_type is required"}

    ev = _events.get(event_type)
    if ev is None:
        return {"isError": True, "text": f"Unknown event type: {event_type!r}"}

    # Validate required params
    for pname, pspec in ev.params.items():
        if isinstance(pspec, dict) and pspec.get("required", False):
            if pname not in params:
                return {
                    "isError": True,
                    "text": f"Missing required param {pname!r} for event {event_type!r}",
                }

    sub = Subscription(event_type=event_type, params=params)
    write_signal(_signal_file, sub)
    return {
        "text": (
            f"Subscribed to {event_type}. "
            "Session will suspend and resume when the event fires."
        ),
    }


# ── JSON-RPC dispatch ─────────────────────────────────────────────────────────

_TOOL_DISPATCH: dict[str, Any] = {
    "list_events": _tool_list_events,
    "subscribe": _tool_subscribe,
}


def _respond(req_id: Any, result: Any = None, error: Any = None) -> str:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return json.dumps(msg)


def _dispatch(req: dict[str, Any]) -> str | None:
    req_id = req.get("id")
    method = req.get("method", "")

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

        handler = _TOOL_DISPATCH.get(name)
        if handler is None:
            outcome = {"isError": True, "text": f"Unknown tool: {name!r}"}
        else:
            outcome = handler(tool_args)

        result = {
            "content": [{"type": "text", "text": outcome["text"]}],
            **({"isError": True} if outcome.get("isError") else {}),
        }
        return _respond(req_id, result)

    return _respond(
        req_id,
        error={"code": -32601, "message": f"Method not found: {method}"},
    )


# ── Main loop ─────────────────────────────────────────────────────────────────


def main() -> None:
    """Read newline-delimited JSON from stdin, write responses to stdout."""
    global _events, _signal_file

    parser = argparse.ArgumentParser(description="Events MCP stdio server")
    parser.add_argument("--project-root", required=True, help="Project root directory")
    parser.add_argument("--signal-file", required=True, help="Path to signal file")
    parsed = parser.parse_args()

    project_root = Path(parsed.project_root)
    _signal_file = Path(parsed.signal_file)

    # Load config and parse events
    config_path = project_root / "config.yaml"
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    else:
        config = {}
    _events = load_events(config)

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
                f"events_mcp: dispatch error: {exc}",
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
