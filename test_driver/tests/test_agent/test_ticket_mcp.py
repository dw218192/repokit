"""Tests for the ticket MCP stdio server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_tools.agent.ticket_mcp import (
    _dispatch,
    _tool_create_ticket,
    _tool_get_ticket,
    _tool_list_tickets,
    _tool_update_ticket,
    _validate_ticket,
    _validate_transition,
)


@pytest.fixture
def project(tmp_path):
    return tmp_path


def _call_tool(root: Path, name: str, arguments: dict) -> dict:
    """Simulate a tools/call JSON-RPC request and return the parsed result."""
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    raw = _dispatch(root, req)
    assert raw is not None
    resp = json.loads(raw)
    result = resp["result"]
    text = result["content"][0]["text"]
    is_error = result.get("isError", False)
    return {"text": text, "isError": is_error}


def _advance_ticket(project, ticket_id, to_status):
    """Walk a ticket through valid transitions to reach *to_status*."""
    chain = {
        "todo": [],
        "in_progress": [("in_progress", {})],
        "verify": [("in_progress", {}), ("verify", {})],
    }
    for status, extra_fields in chain[to_status]:
        args = {"ticket_id": ticket_id, "status": status, **extra_fields}
        result = _tool_update_ticket(project, args)
        assert not result.get("isError"), result["text"]


# ── Schema validation ────────────────────────────────────────────


class TestSchemaValidation:
    def test_valid_ticket_passes(self):
        data = {
            "ticket": {"id": "G1_1", "title": "T", "description": "D", "status": "todo"},
            "criteria": [{"criterion": "c1", "met": False}],
            "progress": {"notes": ""},
            "review": {"result": "", "feedback": ""},
        }
        assert _validate_ticket(data) is None

    def test_valid_ticket_no_criteria(self):
        data = {
            "ticket": {"id": "G1_1", "title": "T", "description": "D", "status": "todo"},
            "progress": {"notes": ""},
            "review": {"result": "", "feedback": ""},
        }
        assert _validate_ticket(data) is None

    def test_missing_ticket_section(self):
        data = {"progress": {"notes": ""}, "review": {"result": "", "feedback": ""}}
        assert _validate_ticket(data) is not None

    def test_missing_ticket_id(self):
        data = {
            "ticket": {"title": "T", "description": "D", "status": "todo"},
            "progress": {"notes": ""},
            "review": {"result": "", "feedback": ""},
        }
        assert "ticket.id" in _validate_ticket(data)

    def test_empty_title(self):
        data = {
            "ticket": {"id": "G1_1", "title": "", "description": "D", "status": "todo"},
            "progress": {"notes": ""},
            "review": {"result": "", "feedback": ""},
        }
        assert "ticket.title" in _validate_ticket(data)

    def test_bad_status(self):
        data = {
            "ticket": {"id": "G1_1", "title": "T", "description": "D", "status": "invalid"},
            "progress": {"notes": ""},
            "review": {"result": "", "feedback": ""},
        }
        assert "ticket.status" in _validate_ticket(data)

    def test_bad_criteria_structure(self):
        data = {
            "ticket": {"id": "G1_1", "title": "T", "description": "D", "status": "todo"},
            "criteria": ["just a string"],
            "progress": {"notes": ""},
            "review": {"result": "", "feedback": ""},
        }
        assert "criteria[0]" in _validate_ticket(data)

    def test_criteria_missing_met(self):
        data = {
            "ticket": {"id": "G1_1", "title": "T", "description": "D", "status": "todo"},
            "criteria": [{"criterion": "c1"}],
            "progress": {"notes": ""},
            "review": {"result": "", "feedback": ""},
        }
        assert "criteria[0].met" in _validate_ticket(data)

    def test_bad_review_result(self):
        data = {
            "ticket": {"id": "G1_1", "title": "T", "description": "D", "status": "todo"},
            "progress": {"notes": ""},
            "review": {"result": "invalid", "feedback": ""},
        }
        assert "review.result" in _validate_ticket(data)

    def test_missing_progress(self):
        data = {
            "ticket": {"id": "G1_1", "title": "T", "description": "D", "status": "todo"},
            "review": {"result": "", "feedback": ""},
        }
        assert "progress" in _validate_ticket(data)


# ── Status transitions ───────────────────────────────────────────


class TestStatusTransitions:
    def _make_data(self, status="todo", result="", criteria_met=True):
        criteria = [{"criterion": "c1", "met": criteria_met}]
        return {
            "ticket": {"id": "X", "title": "T", "description": "D", "status": status},
            "criteria": criteria,
            "progress": {"notes": ""},
            "review": {"result": result, "feedback": "f"},
        }

    @pytest.mark.parametrize("current,target,result,met", [
        ("todo", "in_progress", "", True),
        ("todo", "verify", "", True),
        ("in_progress", "verify", "", True),
        ("verify", "closed", "pass", True),
        ("verify", "todo", "fail", True),
    ])
    def test_valid_transitions(self, current, target, result, met):
        data = self._make_data(status=current, result=result, criteria_met=met)
        assert _validate_transition(current, target, data) is None

    @pytest.mark.parametrize("current,target", [
        ("todo", "closed"),
        ("in_progress", "todo"),
        ("in_progress", "closed"),
        ("closed", "todo"),
        ("closed", "in_progress"),
        ("closed", "verify"),
    ])
    def test_invalid_transitions(self, current, target):
        data = self._make_data(status=current)
        err = _validate_transition(current, target, data)
        assert err is not None
        assert "invalid transition" in err

    def test_close_requires_pass(self):
        data = self._make_data(status="verify", result="fail", criteria_met=True)
        err = _validate_transition("verify", "closed", data)
        assert err is not None
        assert "review.result must be 'pass'" in err

    def test_close_requires_criteria_met(self):
        data = self._make_data(status="verify", result="pass", criteria_met=False)
        err = _validate_transition("verify", "closed", data)
        assert err is not None
        assert "unmet criteria" in err

    def test_reopen_requires_fail(self):
        data = self._make_data(status="verify", result="pass", criteria_met=True)
        err = _validate_transition("verify", "todo", data)
        assert err is not None
        assert "review.result must be 'fail'" in err

    def test_update_enforces_transition(self, project):
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
        })
        # todo -> closed is not allowed
        result = _tool_update_ticket(project, {
            "ticket_id": "G1_1", "status": "closed",
        })
        assert result.get("isError")
        assert "invalid transition" in result["text"]

    def test_update_allows_valid_transition(self, project):
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
        })
        # todo -> in_progress is allowed
        result = _tool_update_ticket(project, {
            "ticket_id": "G1_1", "status": "in_progress",
        })
        assert not result.get("isError")

    def test_closed_is_terminal(self, project):
        """Closed tickets cannot be transitioned to any status."""
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
            "criteria": ["c1"],
        })
        _advance_ticket(project, "G1_1", "verify")
        # Mark criteria met and close via review pass
        path = project / "_agent" / "tickets" / "G1_1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["criteria"][0]["met"] = True
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        _tool_update_ticket(project, {
            "ticket_id": "G1_1", "status": "closed", "result": "pass",
        })
        # Now try to reopen — should fail
        result = _tool_update_ticket(project, {
            "ticket_id": "G1_1", "status": "todo",
        })
        assert result.get("isError")
        assert "invalid transition" in result["text"]

    def test_close_with_unmet_criteria_rejected(self, project):
        """Cannot close a ticket when criteria are not met."""
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
            "criteria": ["c1"],
        })
        _advance_ticket(project, "G1_1", "verify")
        result = _tool_update_ticket(project, {
            "ticket_id": "G1_1", "status": "closed", "result": "pass",
        })
        assert result.get("isError")
        assert "unmet criteria" in result["text"]

    def test_close_without_pass_rejected(self, project):
        """Cannot close a ticket without review.result == 'pass'."""
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
        })
        _advance_ticket(project, "G1_1", "verify")
        result = _tool_update_ticket(project, {
            "ticket_id": "G1_1", "status": "closed",
        })
        assert result.get("isError")
        assert "review.result must be 'pass'" in result["text"]


# ── create_ticket ────────────────────────────────────────────────


class TestCreateTicket:
    def test_creates_ticket_file(self, project):
        result = _tool_create_ticket(project, {
            "id": "G1_1",
            "title": "Add X support",
            "description": "Detailed instructions here",
        })
        assert not result.get("isError")
        ticket_path = project / "_agent" / "tickets" / "G1_1.json"
        assert ticket_path.exists()
        data = json.loads(ticket_path.read_text(encoding="utf-8"))
        assert data["ticket"]["id"] == "G1_1"
        assert data["ticket"]["title"] == "Add X support"
        assert data["ticket"]["status"] == "todo"

    def test_creates_ticket_with_criteria(self, project):
        result = _tool_create_ticket(project, {
            "id": "G1_1",
            "title": "Add X support",
            "description": "Detailed instructions here",
            "criteria": ["Button renders on /login", "Click redirects to OAuth"],
        })
        assert not result.get("isError")
        ticket_path = project / "_agent" / "tickets" / "G1_1.json"
        data = json.loads(ticket_path.read_text(encoding="utf-8"))
        assert len(data["criteria"]) == 2
        assert data["criteria"][0]["criterion"] == "Button renders on /login"
        assert data["criteria"][0]["met"] is False
        assert data["criteria"][1]["criterion"] == "Click redirects to OAuth"

    def test_roundtrips_through_validation(self, project):
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "T", "description": "D",
            "criteria": ["c1"],
        })
        ticket_path = project / "_agent" / "tickets" / "G1_1.json"
        data = json.loads(ticket_path.read_text(encoding="utf-8"))
        assert _validate_ticket(data) is None

    def test_duplicate_ticket_errors(self, project):
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
        })
        result = _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
        })
        assert result.get("isError")
        assert "already exists" in result["text"]

    def test_auto_creates_tickets_dir(self, project):
        """create_ticket auto-creates _agent/tickets/ if missing."""
        assert not (project / "_agent" / "tickets").exists()
        result = _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
        })
        assert not result.get("isError")
        assert (project / "_agent" / "tickets" / "G1_1.json").exists()

    def test_rejects_empty_title(self, project):
        result = _tool_create_ticket(project, {
            "id": "G1_1", "title": "", "description": "d",
        })
        assert result.get("isError")
        assert "Validation error" in result["text"]


# ── list_tickets ─────────────────────────────────────────────────


class TestListTickets:
    def test_lists_tickets_with_status(self, project):
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "t1", "description": "d",
        })
        _tool_create_ticket(project, {
            "id": "G1_2", "title": "t2", "description": "d",
        })

        result = _tool_list_tickets(project, {})
        assert not result.get("isError")
        tickets = json.loads(result["text"])
        assert len(tickets) == 2
        ids = {t["id"] for t in tickets}
        assert ids == {"G1_1", "G1_2"}
        assert all(t["status"] == "todo" for t in tickets)

    def test_empty_returns_empty_list(self, project):
        result = _tool_list_tickets(project, {})
        tickets = json.loads(result["text"])
        assert tickets == []

    def test_auto_creates_tickets_dir(self, project):
        """list_tickets auto-creates _agent/tickets/ if missing."""
        assert not (project / "_agent" / "tickets").exists()
        result = _tool_list_tickets(project, {})
        assert not result.get("isError")
        assert (project / "_agent" / "tickets").is_dir()


# ── get_ticket ───────────────────────────────────────────────────


class TestGetTicket:
    def test_returns_full_content(self, project):
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "Title", "description": "Desc",
        })

        result = _tool_get_ticket(project, {"ticket_id": "G1_1"})
        assert not result.get("isError")
        data = json.loads(result["text"])
        assert data["ticket"]["id"] == "G1_1"
        assert data["ticket"]["title"] == "Title"

    def test_missing_ticket_errors(self, project):
        result = _tool_get_ticket(project, {"ticket_id": "nope"})
        assert result.get("isError")
        assert "not found" in result["text"]


# ── update_ticket ────────────────────────────────────────────────


class TestUpdateTicket:
    def test_updates_status(self, project):
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
        })

        result = _tool_update_ticket(project, {
            "ticket_id": "G1_1", "status": "in_progress",
        })
        assert not result.get("isError")

        data = json.loads(
            (project / "_agent" / "tickets" / "G1_1.json").read_text(encoding="utf-8")
        )
        assert data["ticket"]["status"] == "in_progress"

    def test_updates_multiple_fields(self, project):
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
            "criteria": ["c1"],
        })
        _advance_ticket(project, "G1_1", "verify")

        # Mark criteria met
        path = project / "_agent" / "tickets" / "G1_1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["criteria"][0]["met"] = True
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        result = _tool_update_ticket(project, {
            "ticket_id": "G1_1",
            "status": "closed", "result": "pass", "feedback": "Looks good",
        })
        assert not result.get("isError")

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["ticket"]["status"] == "closed"
        assert data["review"]["result"] == "pass"
        assert data["review"]["feedback"] == "Looks good"

    def test_missing_ticket_errors(self, project):
        result = _tool_update_ticket(project, {
            "ticket_id": "nope", "status": "closed",
        })
        assert result.get("isError")

    def test_no_updates_returns_message(self, project):
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
        })

        result = _tool_update_ticket(project, {
            "ticket_id": "G1_1",
        })
        assert "No fields" in result["text"]

    def test_same_status_no_transition_check(self, project):
        """Setting status to the same value should not trigger transition check."""
        _tool_create_ticket(project, {
            "id": "G1_1", "title": "t", "description": "d",
        })
        result = _tool_update_ticket(project, {
            "ticket_id": "G1_1", "status": "todo",
        })
        assert not result.get("isError")


# ── JSON-RPC dispatch ────────────────────────────────────────────


class TestDispatch:
    def test_initialize(self, project):
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        raw = _dispatch(project, req)
        resp = json.loads(raw)
        assert resp["result"]["serverInfo"]["name"] == "tickets"

    def test_tools_list(self, project):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        raw = _dispatch(project, req)
        resp = json.loads(raw)
        tool_names = {t["name"] for t in resp["result"]["tools"]}
        assert "list_tickets" in tool_names
        assert "get_ticket" in tool_names
        assert "create_ticket" in tool_names
        assert "update_ticket" in tool_names
        assert "init_workstream" not in tool_names

    def test_notification_no_response(self, project):
        req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert _dispatch(project, req) is None

    def test_unknown_method(self, project):
        req = {"jsonrpc": "2.0", "id": 1, "method": "bogus"}
        raw = _dispatch(project, req)
        resp = json.loads(raw)
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_tools_call_roundtrip(self, project):
        """Full roundtrip: create_ticket via JSON-RPC dispatch."""
        result = _call_tool(project, "create_ticket", {
            "id": "G1_1", "title": "t", "description": "d",
        })
        assert not result["isError"]
        assert (project / "_agent" / "tickets" / "G1_1.json").exists()

    def test_unknown_tool(self, project):
        result = _call_tool(project, "nonexistent", {})
        assert result["isError"]
        assert "Unknown tool" in result["text"]
