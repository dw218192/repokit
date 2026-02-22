"""Tests for the ticket MCP stdio server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_tools.agent.ticket_mcp import (
    _dispatch,
    _tool_create_ticket,
    _tool_delete_ticket,
    _tool_get_ticket,
    _tool_list_tickets,
    _tool_mark_criteria,
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
        assert "reset_ticket" in tool_names
        assert "mark_criteria" in tool_names
        assert "delete_ticket" in tool_names
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


# ── mark_criteria ────────────────────────────────────────────────


class TestMarkCriteria:
    def _make_ticket(self, project, criteria):
        _tool_create_ticket(project, {
            "id": "T1", "title": "t", "description": "d",
            "criteria": criteria,
        })

    def _read_criteria(self, project):
        path = project / "_agent" / "tickets" / "T1.json"
        return json.loads(path.read_text(encoding="utf-8"))["criteria"]

    def test_mark_single_criterion(self, project):
        self._make_ticket(project, ["c0", "c1", "c2"])
        result = _tool_mark_criteria(project, {"ticket_id": "T1", "indices": [1]})
        assert not result.get("isError")
        criteria = self._read_criteria(project)
        assert criteria[0]["met"] is False
        assert criteria[1]["met"] is True
        assert criteria[2]["met"] is False

    def test_mark_multiple_criteria(self, project):
        self._make_ticket(project, ["c0", "c1", "c2"])
        result = _tool_mark_criteria(project, {"ticket_id": "T1", "indices": [0, 2]})
        assert not result.get("isError")
        criteria = self._read_criteria(project)
        assert criteria[0]["met"] is True
        assert criteria[1]["met"] is False
        assert criteria[2]["met"] is True

    def test_mark_as_unmet(self, project):
        self._make_ticket(project, ["c0"])
        _tool_mark_criteria(project, {"ticket_id": "T1", "indices": [0]})
        result = _tool_mark_criteria(project, {
            "ticket_id": "T1", "indices": [0], "met": False,
        })
        assert not result.get("isError")
        assert self._read_criteria(project)[0]["met"] is False

    def test_default_met_is_true(self, project):
        self._make_ticket(project, ["c0"])
        result = _tool_mark_criteria(project, {"ticket_id": "T1", "indices": [0]})
        assert not result.get("isError")
        assert self._read_criteria(project)[0]["met"] is True

    def test_index_out_of_range(self, project):
        self._make_ticket(project, ["c0"])
        result = _tool_mark_criteria(project, {"ticket_id": "T1", "indices": [5]})
        assert result.get("isError")
        assert "out of range" in result["text"]

    def test_negative_index(self, project):
        self._make_ticket(project, ["c0"])
        result = _tool_mark_criteria(project, {"ticket_id": "T1", "indices": [-1]})
        assert result.get("isError")
        assert "Invalid index" in result["text"]

    def test_no_criteria_on_ticket(self, project):
        _tool_create_ticket(project, {"id": "T1", "title": "t", "description": "d"})
        result = _tool_mark_criteria(project, {"ticket_id": "T1", "indices": [0]})
        assert result.get("isError")
        assert "no criteria" in result["text"]

    def test_empty_indices(self, project):
        self._make_ticket(project, ["c0"])
        result = _tool_mark_criteria(project, {"ticket_id": "T1", "indices": []})
        assert result.get("isError")
        assert "empty" in result["text"]

    def test_missing_ticket(self, project):
        result = _tool_mark_criteria(project, {"ticket_id": "nope", "indices": [0]})
        assert result.get("isError")
        assert "not found" in result["text"]

    def test_invalid_ticket_id(self, project):
        result = _tool_mark_criteria(project, {"ticket_id": "../bad", "indices": [0]})
        assert result.get("isError")
        assert "invalid characters" in result["text"]

    def test_atomicity_bad_index_prevents_mutation(self, project):
        self._make_ticket(project, ["c0", "c1"])
        result = _tool_mark_criteria(project, {
            "ticket_id": "T1", "indices": [0, 99],
        })
        assert result.get("isError")
        criteria = self._read_criteria(project)
        assert criteria[0]["met"] is False
        assert criteria[1]["met"] is False

    def test_dispatch_roundtrip(self, project):
        self._make_ticket(project, ["c0"])
        result = _call_tool(project, "mark_criteria", {
            "ticket_id": "T1", "indices": [0],
        })
        assert not result["isError"]
        assert self._read_criteria(project)[0]["met"] is True


# ── delete_ticket ────────────────────────────────────────────────


class TestDeleteTicket:
    def test_deletes_existing_ticket(self, project):
        _tool_create_ticket(project, {"id": "T1", "title": "t", "description": "d"})
        result = _tool_delete_ticket(project, {"ticket_id": "T1"})
        assert not result.get("isError")
        assert not (project / "_agent" / "tickets" / "T1.json").exists()

    def test_missing_ticket(self, project):
        result = _tool_delete_ticket(project, {"ticket_id": "nope"})
        assert result.get("isError")
        assert "not found" in result["text"]

    def test_invalid_id(self, project):
        result = _tool_delete_ticket(project, {"ticket_id": "../bad"})
        assert result.get("isError")
        assert "invalid characters" in result["text"]

    def test_deleted_absent_from_list(self, project):
        _tool_create_ticket(project, {"id": "T1", "title": "t", "description": "d"})
        _tool_delete_ticket(project, {"ticket_id": "T1"})
        tickets = json.loads(_tool_list_tickets(project, {})["text"])
        assert all(t["id"] != "T1" for t in tickets)

    def test_delete_then_recreate(self, project):
        _tool_create_ticket(project, {"id": "T1", "title": "t", "description": "d"})
        _tool_delete_ticket(project, {"ticket_id": "T1"})
        result = _tool_create_ticket(project, {"id": "T1", "title": "t2", "description": "d2"})
        assert not result.get("isError")
        data = json.loads(
            (project / "_agent" / "tickets" / "T1.json").read_text(encoding="utf-8")
        )
        assert data["ticket"]["title"] == "t2"

    def test_dispatch_roundtrip(self, project):
        _tool_create_ticket(project, {"id": "T1", "title": "t", "description": "d"})
        result = _call_tool(project, "delete_ticket", {"ticket_id": "T1"})
        assert not result["isError"]
        assert not (project / "_agent" / "tickets" / "T1.json").exists()


# ── corruption handling ──────────────────────────────────────────


class TestCorruptionHandling:
    def test_list_invalid_json(self, project):
        tdir = project / "_agent" / "tickets"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "bad.json").write_text("not json{{{", encoding="utf-8")
        tickets = json.loads(_tool_list_tickets(project, {})["text"])
        assert len(tickets) == 1
        assert tickets[0]["id"] == "bad"
        assert tickets[0]["status"] == "invalid"
        assert "invalid JSON" in tickets[0]["error"]

    def test_list_bad_schema(self, project):
        tdir = project / "_agent" / "tickets"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "bad.json").write_text('{"not": "a ticket"}', encoding="utf-8")
        tickets = json.loads(_tool_list_tickets(project, {})["text"])
        assert len(tickets) == 1
        assert tickets[0]["status"] == "invalid"
        assert "bad schema" in tickets[0]["error"]

    def test_list_mixed_valid_and_invalid(self, project):
        _tool_create_ticket(project, {"id": "good", "title": "t", "description": "d"})
        tdir = project / "_agent" / "tickets"
        (tdir / "bad.json").write_text("{{broken", encoding="utf-8")
        tickets = json.loads(_tool_list_tickets(project, {})["text"])
        by_id = {t["id"]: t for t in tickets}
        assert by_id["good"]["status"] == "todo"
        assert "error" not in by_id["good"]
        assert by_id["bad"]["status"] == "invalid"
        assert "error" in by_id["bad"]

    def test_valid_ticket_has_no_error_field(self, project):
        _tool_create_ticket(project, {"id": "ok", "title": "t", "description": "d"})
        tickets = json.loads(_tool_list_tickets(project, {})["text"])
        assert "error" not in tickets[0]

    def test_get_ticket_invalid_json(self, project):
        tdir = project / "_agent" / "tickets"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "bad.json").write_text("not json", encoding="utf-8")
        result = _tool_get_ticket(project, {"ticket_id": "bad"})
        assert result.get("isError")
        assert "invalid JSON" in result["text"]


# ── required criteria (config.yaml) ─────────────────────────────


def _write_config(project, required_criteria):
    """Write a config.yaml with agent.required_criteria."""
    import yaml
    data = {"agent": {"required_criteria": required_criteria}}
    (project / "config.yaml").write_text(yaml.dump(data), encoding="utf-8")


class TestRequiredCriteria:
    def test_required_criteria_added(self, project):
        _write_config(project, ["Tests pass", "No regressions"])
        result = _tool_create_ticket(project, {
            "id": "T1", "title": "t", "description": "d",
        })
        assert not result.get("isError")
        path = project / "_agent" / "tickets" / "T1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        texts = [c["criterion"] for c in data["criteria"]]
        assert "Tests pass" in texts
        assert "No regressions" in texts

    def test_merged_with_user_criteria(self, project):
        _write_config(project, ["Tests pass"])
        result = _tool_create_ticket(project, {
            "id": "T1", "title": "t", "description": "d",
            "criteria": ["Custom check"],
        })
        assert not result.get("isError")
        path = project / "_agent" / "tickets" / "T1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        texts = [c["criterion"] for c in data["criteria"]]
        assert texts == ["Custom check", "Tests pass"]

    def test_deduplication(self, project):
        _write_config(project, ["Tests pass"])
        result = _tool_create_ticket(project, {
            "id": "T1", "title": "t", "description": "d",
            "criteria": ["Tests pass", "Other"],
        })
        assert not result.get("isError")
        path = project / "_agent" / "tickets" / "T1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        texts = [c["criterion"] for c in data["criteria"]]
        assert texts == ["Tests pass", "Other"]

    def test_no_config_file(self, project):
        """No config.yaml → no required criteria, no error."""
        result = _tool_create_ticket(project, {
            "id": "T1", "title": "t", "description": "d",
        })
        assert not result.get("isError")
        path = project / "_agent" / "tickets" / "T1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["criteria"] == []

    def test_empty_required_list(self, project):
        _write_config(project, [])
        result = _tool_create_ticket(project, {
            "id": "T1", "title": "t", "description": "d",
            "criteria": ["User criterion"],
        })
        assert not result.get("isError")
        path = project / "_agent" / "tickets" / "T1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        texts = [c["criterion"] for c in data["criteria"]]
        assert texts == ["User criterion"]

    def test_all_criteria_start_unmet(self, project):
        _write_config(project, ["Tests pass"])
        _tool_create_ticket(project, {
            "id": "T1", "title": "t", "description": "d",
        })
        path = project / "_agent" / "tickets" / "T1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert all(c["met"] is False for c in data["criteria"])
