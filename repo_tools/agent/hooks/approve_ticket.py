"""PreToolUse hook that requests human approval for ticket creation.

Merges required criteria into the tool input so Claude Code's native
permission prompt shows the complete ticket to the user.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import write_log


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve ticket creation with merged criteria.")
    parser.add_argument("--required-criteria", default="[]", help="JSON list of required criteria")
    parser.add_argument("--debug-log", default=None, help="Append hook decisions to this file")
    args = parser.parse_args()

    log_path = Path(args.debug_log) if args.debug_log else None
    required_criteria: list[str] = json.loads(args.required_criteria)

    event = json.load(sys.stdin)
    tool_input: dict = event.get("tool_input", {})

    # Merge required criteria (dedup) — same logic as tickets._tool_create_ticket
    criteria: list[str] = list(tool_input.get("criteria", []))
    seen = set(criteria)
    for rc in required_criteria:
        if rc not in seen:
            criteria.append(rc)
            seen.add(rc)

    updated_input = dict(tool_input)
    updated_input["criteria"] = criteria

    if log_path:
        tid = tool_input.get("id", "unknown")
        write_log(log_path, f"create_ticket({tid})", "ask", "human review")

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "updatedInput": updated_input,
            }
        },
        sys.stdout,
    )
