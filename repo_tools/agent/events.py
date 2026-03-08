"""Event-driven session wrapper.

Agents subscribe to named events; the outer ``./repo agent`` wrapper polls
until an event fires, then resumes the session with the event payload.

All state is in-memory — no persistence files.
"""

from __future__ import annotations

import shlex
import time
from pathlib import Path
from typing import Any

from ..command_runner import _validate_steps
from ..core import ShellCommand, TokenFormatter, logger

# ── In-memory subscription store ────────────────────────────────────────────

_subscriptions: list[dict[str, Any]] = []


def subscribe(
    group: str,
    event: str,
    config: dict[str, Any],
    tokens: dict[str, str] | None = None,
) -> str:
    """Validate and register an event subscription.

    Returns a confirmation message.  Raises ``KeyError`` on bad group/event.
    """
    resolve_event_config(config, group, event)  # validate
    _subscriptions.append({
        "group": group,
        "event": event,
        "tokens": dict(tokens) if tokens else {},
    })
    return f"Subscribed to {group}.{event}. Session will suspend after this turn."


def pop_subscription() -> dict[str, Any] | None:
    """Pop the first subscription (FIFO), or ``None`` if empty."""
    return _subscriptions.pop(0) if _subscriptions else None


def has_subscriptions() -> bool:
    return len(_subscriptions) > 0


def clear_subscriptions() -> None:
    _subscriptions.clear()


def load_signal_file(path: Path) -> None:
    """Load a subscription from a signal file (CLI backend) and delete it."""
    if not path.exists():
        return
    try:
        import json
        sub = json.loads(path.read_text(encoding="utf-8"))
        _subscriptions.append(sub)
        path.unlink()
    except (OSError, json.JSONDecodeError, ValueError):
        logger.warning("Failed to load event signal file %s", path, exc_info=True)


# ── Config lookup ────────────────────────────────────────────────────────────


def resolve_event_config(
    config: dict[str, Any],
    group: str,
    event: str,
) -> dict[str, Any]:
    """Look up ``config["agent"]["events"][group][event]``.

    Raises ``KeyError`` with a descriptive message on miss.
    """
    events = config.get("agent", config).get("events", {})
    if group not in events:
        available = ", ".join(sorted(events)) or "(none)"
        raise KeyError(f"Unknown event group {group!r}; available: {available}")
    group_cfg = events[group]
    if event not in group_cfg:
        available = ", ".join(sorted(group_cfg)) or "(none)"
        raise KeyError(
            f"Unknown event {event!r} in group {group!r}; available: {available}"
        )
    return group_cfg[event]


def list_events_text(config: dict[str, Any]) -> str:
    """Compact single-line summary of all configured event groups/events."""
    events = config.get("agent", config).get("events", {})
    if not events:
        return "No events configured."
    parts: list[str] = []
    for group_name in sorted(events):
        group = events[group_name]
        event_parts = []
        for ev_name in sorted(group):
            ev = group[ev_name]
            desc = ev.get("description", "")
            label = f"{ev_name} ({desc})" if desc else ev_name
            event_parts.append(label)
        parts.append(f"{group_name}: {', '.join(event_parts)}")
    return " | ".join(parts)


# ── Step execution (reuses CommandRunnerTool infra) ──────────────────────────


def run_event_steps(
    steps_raw: list[Any],
    tokens: dict[str, str],
    config: dict[str, Any],
    cwd: str | Path,
) -> tuple[int, str]:
    """Run steps through the same infra as CommandRunnerTool.

    Returns ``(exit_code, stdout)``.
    """
    formatter = TokenFormatter(tokens, config)
    steps = _validate_steps("event", steps_raw)
    last_stdout = ""
    for step in steps:
        cmd_str = formatter.resolve(step["command"])
        step_cwd = Path(cwd) if step.get("cwd") is None else Path(formatter.resolve(step["cwd"]))
        proc = ShellCommand(
            shlex.split(cmd_str),
            cwd=step_cwd,
        ).run(capture_output=True, text=True)
        last_stdout = proc.stdout or ""
        if proc.returncode != 0:
            return proc.returncode, last_stdout
    return 0, last_stdout


# ── Poll / payload ──────────────────────────────────────────────────────────


def poll_until_fired(
    event_cfg: dict[str, Any],
    tokens: dict[str, str],
    config: dict[str, Any],
    cwd: str | Path,
) -> None:
    """Block until poll steps return exit code 0."""
    interval = event_cfg.get("interval", 60)
    while True:
        rc, _ = run_event_steps(event_cfg["poll"], tokens, config, cwd)
        if rc == 0:
            return
        logger.debug(f"Poll returned {rc}, sleeping {interval}s")
        time.sleep(interval)


def run_payload(
    event_cfg: dict[str, Any],
    tokens: dict[str, str],
    config: dict[str, Any],
    cwd: str | Path,
) -> str:
    """Run payload steps, return stdout."""
    _, stdout = run_event_steps(event_cfg["payload"], tokens, config, cwd)
    return stdout.strip()


# ── MCP tool schemas ────────────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_events",
        "description": "List available event groups and events for subscription",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "subscribe_event",
        "description": (
            "Subscribe to a named event. Session suspends after this turn; "
            "resumes when event fires."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": "Event group name",
                },
                "event": {
                    "type": "string",
                    "description": "Event name within the group",
                },
                "tokens": {
                    "type": "object",
                    "description": "Additional tokens for poll/payload commands",
                    "default": {},
                },
            },
            "required": ["group", "event"],
        },
    },
]
