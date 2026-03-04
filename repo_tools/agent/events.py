"""Event channel data model, config parser, and polling engine.

Provides the core event abstractions used by the MCP server
(``events_mcp.py``) and the event loop in ``tool.py``.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EventDef:
    """Definition of a subscribable event loaded from config.yaml."""

    group: str
    name: str
    doc: str
    params: dict[str, Any]
    poll: str
    payload: str
    detect: str = "exit"
    poll_interval: int = 30


@dataclass
class Subscription:
    """A concrete subscription to a specific event with bound parameters."""

    event_type: str
    params: dict[str, Any] = field(default_factory=dict)


def _parse_events_section(events_section: dict[str, Any]) -> dict[str, EventDef]:
    """Parse a nested group/event dict into a flat ``{dotted: EventDef}`` map."""
    if not isinstance(events_section, dict):
        return {}

    result: dict[str, EventDef] = {}
    for group_name, group_dict in events_section.items():
        if not isinstance(group_dict, dict):
            continue
        for event_name, event_dict in group_dict.items():
            if not isinstance(event_dict, dict):
                continue
            dotted = f"{group_name}.{event_name}"
            result[dotted] = EventDef(
                group=group_name,
                name=event_name,
                doc=event_dict.get("doc", ""),
                params=event_dict.get("params", {}),
                poll=event_dict.get("poll", ""),
                payload=event_dict.get("payload", ""),
                detect=event_dict.get("detect", "exit"),
                poll_interval=int(event_dict.get("poll_interval", 30)),
            )
    return result


def load_events(config: dict[str, Any]) -> dict[str, EventDef]:
    """Parse the ``events:`` section of an already-merged config.

    Returns a dict keyed by dotted name ``"group.name"``.
    Built-in events are included via the framework defaults layer in
    ``load_config()`` — this function is a pure config parser with no file I/O.
    """
    return _parse_events_section(config.get("events", {}))


def expand_command(template: str, params: dict[str, Any]) -> str:
    """Substitute ``{param}`` placeholders in poll/payload commands."""
    result = template
    for key, value in params.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


# ── Polling engine ────────────────────────────────────────────────


def poll_exit(cmd: str, cwd: Path) -> int:
    """Run a blocking poll command and return its exit code.

    The command is expected to block until the event fires (e.g. ``gh run watch``).
    Returns 0 when the event fired, non-zero on error/timeout.
    """
    result = subprocess.run(cmd, shell=True, cwd=str(cwd))
    return result.returncode


def poll_delta(cmd: str, interval: int, cwd: Path) -> str:
    """Poll a command repeatedly until its stdout changes.

    Captures stdout on each invocation, sleeps *interval* seconds between
    runs, and returns the new stdout value once it differs from the initial.
    """
    initial = subprocess.run(
        cmd, shell=True, cwd=str(cwd), capture_output=True, text=True,
    ).stdout
    while True:
        time.sleep(interval)
        current = subprocess.run(
            cmd, shell=True, cwd=str(cwd), capture_output=True, text=True,
        ).stdout
        if current != initial:
            return current


def collect_payload(cmd: str, cwd: Path) -> str:
    """Run a payload command and return its stripped stdout.

    On failure (non-zero exit), returns stderr or a descriptive error string.
    """
    result = subprocess.run(
        cmd, shell=True, cwd=str(cwd), capture_output=True, text=True,
    )
    if result.returncode != 0:
        return result.stderr.strip() or f"Payload command failed (exit {result.returncode})"
    return result.stdout.strip()


def poll_for_event(event_def: EventDef, subscription: Subscription, cwd: Path) -> str:
    """Wait for an event and return its payload.

    Expands parameter placeholders in poll/payload commands, dispatches to the
    appropriate detection strategy, then collects and returns the payload.
    """
    expanded_poll = expand_command(event_def.poll, subscription.params)
    expanded_payload = (
        expand_command(event_def.payload, subscription.params)
        if event_def.payload
        else ""
    )

    if event_def.detect == "exit":
        rc = poll_exit(expanded_poll, cwd)
        if rc != 0:
            raise RuntimeError(f"Poll command failed (exit {rc}): {expanded_poll}")
    elif event_def.detect == "delta":
        poll_delta(expanded_poll, event_def.poll_interval, cwd)
    else:
        raise ValueError(f"Unknown detect mode: {event_def.detect!r}")

    if expanded_payload:
        return collect_payload(expanded_payload, cwd)
    return "Event fired."
