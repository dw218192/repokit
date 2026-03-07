"""Shared constants used by both SDK and CLI backends."""

from __future__ import annotations

# Tools that are always pre-approved — all are read-only or local edits.
# Bash is excluded here; it is added per-role and gated by the PreToolUse hook.
ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch",
    "AskUserQuestion", "TodoWrite", "Agent", "NotebookEdit",
    "EnterPlanMode", "ExitPlanMode",
]

# JSON schemas for structured headless output, keyed by role.
OUTPUT_SCHEMAS: dict[str, dict] = {
    "worker": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string"},
            "status": {"type": "string", "enum": ["verify", "in_progress"]},
            "notes": {"type": "string"},
        },
        "required": ["ticket_id", "status", "notes"],
        "additionalProperties": False,
    },
    "reviewer": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string"},
            "status": {"type": "string", "enum": ["closed", "todo"]},
            "result": {"type": "string", "enum": ["pass", "fail"]},
            "feedback": {"type": "string"},
            "criteria": {"type": "array", "items": {"type": "boolean"}},
        },
        "required": ["ticket_id", "status", "result", "feedback", "criteria"],
        "additionalProperties": False,
    },
}
