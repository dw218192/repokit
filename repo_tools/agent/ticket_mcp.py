"""Stdio MCP server for ticket CRUD.

Provides tools for creating/listing/reading/updating tickets — all as simple
file operations under ``_agent/tickets/``.

Invoked by Claude Code as::

    python -m repo_tools.agent.ticket_mcp --project-root <path>

Protocol: newline-delimited JSON-RPC 2.0 over stdin/stdout (same as
``coderabbit_mcp_stdio``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "tickets"
_SERVER_VERSION = "0.1"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_VALID_STATUSES = {"todo", "in_progress", "verify", "closed"}
_VALID_RESULTS = {"", "pass", "fail"}

_ALLOWED_TRANSITIONS = {
    "todo":        {"in_progress", "verify"},
    "in_progress": {"verify"},
    "verify":      {"closed", "todo"},
    "closed":      set(),
}

_ROLE_ALLOWED_TOOLS: dict[str, set[str]] = {
    "orchestrator": {
        "list_tickets", "get_ticket", "create_ticket", "update_ticket",
        "reset_ticket", "mark_criteria", "delete_ticket",
    },
    "worker": {"list_tickets", "get_ticket", "update_ticket"},
    "reviewer": {"list_tickets", "get_ticket", "update_ticket", "mark_criteria"},
}

_ROLE_UPDATE_FIELDS: dict[str, set[str]] = {
    "orchestrator": {"status", "notes", "result", "feedback", "description"},
    "worker": {"status", "notes"},
    "reviewer": {"status", "result", "feedback"},
}

_ROLE_ALLOWED_TRANSITIONS: dict[str, set[tuple[str, str]]] = {
    "orchestrator": {
        ("todo", "in_progress"),
        ("todo", "verify"),
        ("in_progress", "verify"),
        ("verify", "closed"),
        ("verify", "todo"),
    },
    "worker": {
        ("todo", "in_progress"),
        ("todo", "verify"),
        ("in_progress", "verify"),
    },
    "reviewer": {
        ("verify", "closed"),
        ("verify", "todo"),
    },
}

_TOOLS = [
    {
        "name": "list_tickets",
        "description": "List all tickets with their id and status.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_ticket",
        "description": "Return the full JSON content of a ticket.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Ticket identifier.",
                },
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "create_ticket",
        "description": "Create a new ticket JSON file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Short descriptive kebab-case id (e.g. 'add-auth-hook').",
                },
                "title": {
                    "type": "string",
                    "description": "Short task title.",
                },
                "description": {
                    "type": "string",
                    "description": "Detailed implementation instructions.",
                },
                "criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Acceptance criteria (optional).",
                },
            },
            "required": ["id", "title", "description"],
        },
    },
    {
        "name": "update_ticket",
        "description": (
            "Update fields on an existing ticket. Only provided fields are changed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Ticket identifier.",
                },
                "status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "verify", "closed"],
                    "description": "New ticket status.",
                },
                "notes": {
                    "type": "string",
                    "description": "Progress notes to set.",
                },
                "result": {
                    "type": "string",
                    "enum": ["pass", "fail", ""],
                    "description": "Review result.",
                },
                "feedback": {
                    "type": "string",
                    "description": "Review feedback.",
                },
                "description": {
                    "type": "string",
                    "description": "New ticket description.",
                },
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "reset_ticket",
        "description": "Reset a ticket to 'todo' status, clearing progress and review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Ticket identifier.",
                },
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "mark_criteria",
        "description": "Mark specific acceptance criteria as met or unmet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "Ticket identifier."},
                "indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Zero-based indices of criteria to update.",
                },
                "met": {
                    "type": "boolean",
                    "description": "Whether to mark as met (default true).",
                },
            },
            "required": ["ticket_id", "indices"],
        },
    },
    {
        "name": "delete_ticket",
        "description": "Delete a ticket JSON file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Ticket identifier.",
                },
            },
            "required": ["ticket_id"],
        },
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _validate_id(value: str, field: str) -> str | None:
    """Return error message if *value* is not a safe identifier, else None."""
    if not value:
        return f"{field} must not be empty"
    if not _SAFE_ID_RE.match(value):
        return f"{field} contains invalid characters: {value!r}"
    return None


def _validate_ticket(data: dict) -> str | None:
    """Validate ticket JSON structure. Return error message or None."""
    # ticket section
    ticket = data.get("ticket")
    if not isinstance(ticket, dict):
        return "missing 'ticket' section"
    for field in ("id", "title", "description"):
        val = ticket.get(field)
        if not isinstance(val, str) or not val:
            return f"ticket.{field} must be a non-empty string"
    status = ticket.get("status")
    if status not in _VALID_STATUSES:
        return f"ticket.status must be one of {sorted(_VALID_STATUSES)}, got {status!r}"

    # criteria — optional list
    criteria = data.get("criteria")
    if criteria is not None:
        if not isinstance(criteria, list):
            return "criteria must be a list"
        for i, item in enumerate(criteria):
            if not isinstance(item, dict):
                return f"criteria[{i}] must be an object"
            if not isinstance(item.get("criterion"), str):
                return f"criteria[{i}].criterion must be a string"
            if not isinstance(item.get("met"), bool):
                return f"criteria[{i}].met must be a boolean"

    # progress section
    progress = data.get("progress")
    if not isinstance(progress, dict):
        return "missing 'progress' section"
    if not isinstance(progress.get("notes"), str):
        return "progress.notes must be a string"

    # review section
    review = data.get("review")
    if not isinstance(review, dict):
        return "missing 'review' section"
    result = review.get("result")
    if result not in _VALID_RESULTS:
        return f"review.result must be one of {sorted(_VALID_RESULTS)}, got {result!r}"
    if not isinstance(review.get("feedback"), str):
        return "review.feedback must be a string"

    return None


def _validate_transition(
    current: str, target: str, data: dict, *, role: str | None = None,
) -> str | None:
    """Return error if current->target is not an allowed status transition.

    Also enforces cross-field constraints:
    - verify -> closed requires review.result == "pass" and all criteria met
    - verify -> todo requires review.result == "fail"
    """
    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        return (
            f"invalid transition: {current!r} -> {target!r} "
            f"(allowed: {sorted(allowed)})"
        )

    if role is not None:
        role_allowed = _ROLE_ALLOWED_TRANSITIONS.get(role, set())
        if (current, target) not in role_allowed:
            return f"role {role!r} cannot transition {current!r} -> {target!r}"

    if target == "closed":
        review_result = data.get("review", {}).get("result", "")
        if review_result != "pass":
            return "cannot close ticket: review.result must be 'pass'"
        criteria = data.get("criteria")
        if criteria:
            unmet = [c["criterion"] for c in criteria if not c.get("met")]
            if unmet:
                return f"cannot close ticket: unmet criteria: {unmet}"

    if current == "verify" and target == "todo":
        review_result = data.get("review", {}).get("result", "")
        if review_result != "fail":
            return "cannot reopen from verify: review.result must be 'fail'"

    return None


def _tickets_dir(root: Path) -> Path:
    return root / "_agent" / "tickets"


def _load_required_criteria(root: Path) -> list[str]:
    """Read ``agent.required_criteria`` from config.yaml."""
    config_path = root / "config.yaml"
    if not config_path.exists():
        return []
    try:
        import yaml
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    agent = data.get("agent")
    if not isinstance(agent, dict):
        return []
    criteria = agent.get("required_criteria")
    if not isinstance(criteria, list):
        return []
    return [str(c) for c in criteria]


# ── Tool implementations ─────────────────────────────────────────────────────


def _tool_list_tickets(root: Path, args: dict, *, role: str | None = None) -> dict:
    tdir = _tickets_dir(root)
    if not tdir.is_dir():
        tdir.mkdir(parents=True, exist_ok=True)

    tickets = []
    for f in sorted(tdir.glob("*.json")):
        entry: dict = {"id": f.stem}
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            entry["status"] = "invalid"
            entry["error"] = f"invalid JSON: {exc}"
            tickets.append(entry)
            continue
        if err := _validate_ticket(data):
            entry["status"] = "invalid"
            entry["error"] = f"bad schema: {err}"
        else:
            entry["status"] = data.get("ticket", {}).get("status", "unknown")
        tickets.append(entry)

    return {"text": json.dumps(tickets)}


def _tool_get_ticket(root: Path, args: dict, *, role: str | None = None) -> dict:
    tid = args.get("ticket_id", "").strip()
    if err := _validate_id(tid, "ticket_id"):
        return {"isError": True, "text": err}

    ticket_path = _tickets_dir(root) / f"{tid}.json"
    if not ticket_path.exists():
        return {"isError": True, "text": f"Ticket '{tid}' not found"}

    content = ticket_path.read_text(encoding="utf-8")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {"isError": True, "text": f"Ticket '{tid}' has invalid JSON"}
    if err := _validate_ticket(data):
        return {"isError": True, "text": f"Ticket '{tid}' has invalid schema: {err}"}

    return {"text": content}


def _tool_create_ticket(root: Path, args: dict, *, role: str | None = None) -> dict:
    tid = args.get("id", "").strip()
    if err := _validate_id(tid, "id"):
        return {"isError": True, "text": err}

    tdir = _tickets_dir(root)
    tdir.mkdir(parents=True, exist_ok=True)

    ticket_path = tdir / f"{tid}.json"
    if ticket_path.exists():
        return {"isError": True, "text": f"Ticket '{tid}' already exists"}

    title = args.get("title", "")
    description = args.get("description", "")
    raw_criteria = list(args.get("criteria", []))

    # Merge required criteria (appended, deduplicated)
    seen = set(raw_criteria)
    for rc in _load_required_criteria(root):
        if rc not in seen:
            raw_criteria.append(rc)
            seen.add(rc)

    criteria = [{"criterion": c, "met": False} for c in raw_criteria]

    data = {
        "ticket": {
            "id": tid,
            "title": title,
            "description": description,
            "status": "todo",
        },
        "criteria": criteria,
        "progress": {"notes": ""},
        "review": {"result": "", "feedback": ""},
    }

    if err := _validate_ticket(data):
        return {"isError": True, "text": f"Validation error: {err}"}

    ticket_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"text": f"Ticket '{tid}' created"}


def _tool_update_ticket(root: Path, args: dict, *, role: str | None = None) -> dict:
    tid = args.get("ticket_id", "").strip()
    if err := _validate_id(tid, "ticket_id"):
        return {"isError": True, "text": err}

    ticket_path = _tickets_dir(root) / f"{tid}.json"
    if not ticket_path.exists():
        return {"isError": True, "text": f"Ticket '{tid}' not found"}

    data = json.loads(ticket_path.read_text(encoding="utf-8"))

    updatable = {"status", "notes", "result", "feedback", "description"}
    updates = {k: v for k, v in args.items() if k in updatable and v is not None}
    if not updates:
        return {"text": "No fields to update"}

    if role is not None:
        allowed_fields = _ROLE_UPDATE_FIELDS.get(role, set())
        forbidden = set(updates.keys()) - allowed_fields
        if forbidden:
            return {"isError": True, "text": f"role {role!r} cannot update fields: {sorted(forbidden)}"}

    # Apply non-status fields first so transition checks see the new state
    if "notes" in updates:
        data["progress"]["notes"] = updates["notes"]
    if "result" in updates:
        data["review"]["result"] = updates["result"]
    if "feedback" in updates:
        data["review"]["feedback"] = updates["feedback"]
    if "description" in updates:
        data["ticket"]["description"] = updates["description"]

    # Check status transition if status is being changed
    if "status" in updates:
        current_status = data.get("ticket", {}).get("status", "todo")
        target_status = updates["status"]
        if current_status != target_status:
            if err := _validate_transition(current_status, target_status, data, role=role):
                return {"isError": True, "text": err}
            data["ticket"]["status"] = target_status

    if err := _validate_ticket(data):
        return {"isError": True, "text": f"Validation error after update: {err}"}

    ticket_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"text": f"Ticket '{tid}' updated: {', '.join(updates.keys())}"}


def _tool_reset_ticket(root: Path, args: dict, *, role: str | None = None) -> dict:
    tid = args.get("ticket_id", "").strip()
    if err := _validate_id(tid, "ticket_id"):
        return {"isError": True, "text": err}

    ticket_path = _tickets_dir(root) / f"{tid}.json"
    if not ticket_path.exists():
        return {"isError": True, "text": f"Ticket '{tid}' not found"}

    data = json.loads(ticket_path.read_text(encoding="utf-8"))
    data["ticket"]["status"] = "todo"
    data["progress"] = {"notes": ""}
    data["review"] = {"result": "", "feedback": ""}
    for criterion in data.get("criteria", []):
        criterion["met"] = False

    ticket_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"text": f"Ticket '{tid}' reset to todo"}


def _tool_mark_criteria(root: Path, args: dict, *, role: str | None = None) -> dict:
    tid = args.get("ticket_id", "").strip()
    if err := _validate_id(tid, "ticket_id"):
        return {"isError": True, "text": err}

    indices = args.get("indices", [])
    if not indices:
        return {"isError": True, "text": "indices must not be empty"}

    met = args.get("met", True)

    ticket_path = _tickets_dir(root) / f"{tid}.json"
    if not ticket_path.exists():
        return {"isError": True, "text": f"Ticket '{tid}' not found"}

    data = json.loads(ticket_path.read_text(encoding="utf-8"))
    criteria = data.get("criteria")
    if not criteria:
        return {"isError": True, "text": f"Ticket '{tid}' has no criteria"}

    # Validate all indices before mutating (atomic: all-or-nothing)
    for idx in indices:
        if not isinstance(idx, int) or idx < 0:
            return {"isError": True, "text": f"Invalid index: {idx}"}
        if idx >= len(criteria):
            return {
                "isError": True,
                "text": f"Index {idx} out of range (ticket has {len(criteria)} criteria)",
            }

    for idx in indices:
        criteria[idx]["met"] = met

    ticket_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"text": f"Ticket '{tid}' criteria updated: indices {indices} -> met={met}"}


def _tool_delete_ticket(root: Path, args: dict, *, role: str | None = None) -> dict:
    tid = args.get("ticket_id", "").strip()
    if err := _validate_id(tid, "ticket_id"):
        return {"isError": True, "text": err}

    ticket_path = _tickets_dir(root) / f"{tid}.json"
    if not ticket_path.exists():
        return {"isError": True, "text": f"Ticket '{tid}' not found"}

    ticket_path.unlink()
    return {"text": f"Ticket '{tid}' deleted"}


# ── JSON-RPC dispatch ─────────────────────────────────────────────────────────


_TOOL_DISPATCH = {
    "list_tickets": _tool_list_tickets,
    "get_ticket": _tool_get_ticket,
    "create_ticket": _tool_create_ticket,
    "update_ticket": _tool_update_ticket,
    "reset_ticket": _tool_reset_ticket,
    "mark_criteria": _tool_mark_criteria,
    "delete_ticket": _tool_delete_ticket,
}


def _respond(req_id, result=None, error=None) -> str:
    msg: dict = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return json.dumps(msg)


def _dispatch(root: Path, req: dict, *, role: str | None = None) -> str | None:
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
        elif role is not None and name not in _ROLE_ALLOWED_TOOLS.get(role, set()):
            outcome = {"isError": True, "text": f"role {role!r} cannot use tool {name!r}"}
        else:
            outcome = handler(root, tool_args, role=role)

        result = {
            "content": [{"type": "text", "text": outcome["text"]}],
            **({"isError": True} if outcome.get("isError") else {}),
        }
        return _respond(req_id, result)

    return _respond(req_id, error={"code": -32601, "message": f"Method not found: {method}"})


# ── Main loop ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Ticket MCP stdio server")
    parser.add_argument("--project-root", required=True, help="Project root directory")
    parser.add_argument("--role", default=None, choices=["orchestrator", "worker", "reviewer"],
                        help="Agent role for access control")
    args = parser.parse_args()
    root = Path(args.project_root)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        try:
            response = _dispatch(root, req, role=args.role)
        except Exception as exc:
            print(f"ticket_mcp: dispatch error: {exc}", file=sys.stderr)
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
