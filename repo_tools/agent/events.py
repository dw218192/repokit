"""Event channel data model, config parser, and signal file I/O.

Provides the core event abstractions used by the MCP server
(``events_mcp.py``) and the event loop runner.
"""

from __future__ import annotations

import json
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


def load_events(config: dict[str, Any]) -> dict[str, EventDef]:
    """Parse the ``events:`` section of config.yaml.

    Structure::

        events:
          <group>:
            <event_name>:
              doc: "..."
              params:
                branch: {required: true}
                interval: {default: "60"}
              poll: "git ls-remote {branch}"
              payload: "git log --oneline -5"
              detect: exit          # optional, default "exit"
              poll_interval: 30     # optional, default 30

    Returns a dict keyed by dotted name ``"group.name"``.
    """
    events_section = config.get("events", {})
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


def expand_command(template: str, params: dict[str, Any]) -> str:
    """Substitute ``{param}`` placeholders in poll/payload commands."""
    result = template
    for key, value in params.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def write_signal(path: Path, subscription: Subscription) -> None:
    """Write a subscription as JSON to *path*."""
    data = {"event_type": subscription.event_type, "params": subscription.params}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_signal(path: Path) -> Subscription | None:
    """Read a signal file, delete it, and return a Subscription.

    Returns None if the file doesn't exist.
    """
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()
    return Subscription(
        event_type=data["event_type"],
        params=data.get("params", {}),
    )
