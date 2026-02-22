"""Tests for the ticket reset command."""

from __future__ import annotations

import json

import pytest

from repo_tools.agent.ticket_mcp import _tool_reset_ticket


@pytest.fixture
def project(tmp_path):
    return tmp_path


def _write_ticket(project, ticket_id="G1_1", status="verify", result="", notes="some work"):
    ticket_dir = project / "_agent" / "tickets"
    ticket_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "ticket": {
            "id": ticket_id,
            "title": "Test",
            "description": "Test desc",
            "status": status,
        },
        "criteria": [{"criterion": "c1", "met": True}],
        "progress": {"notes": notes},
        "review": {"result": result, "feedback": "some feedback"},
    }
    path = ticket_dir / f"{ticket_id}.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


class TestResetTicket:
    def test_resets_status_to_todo(self, project):
        path = _write_ticket(project, status="verify")
        result = _tool_reset_ticket(project, {"ticket_id": "G1_1"})
        assert not result.get("isError")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["ticket"]["status"] == "todo"

    def test_clears_progress(self, project):
        path = _write_ticket(project, notes="did stuff")
        _tool_reset_ticket(project, {"ticket_id": "G1_1"})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["progress"]["notes"] == ""

    def test_clears_review(self, project):
        path = _write_ticket(project, result="pass")
        _tool_reset_ticket(project, {"ticket_id": "G1_1"})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["review"]["result"] == ""
        assert data["review"]["feedback"] == ""

    def test_resets_criteria_met(self, project):
        path = _write_ticket(project)
        _tool_reset_ticket(project, {"ticket_id": "G1_1"})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert all(c["met"] is False for c in data["criteria"])

    def test_preserves_ticket_identity(self, project):
        path = _write_ticket(project)
        _tool_reset_ticket(project, {"ticket_id": "G1_1"})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["ticket"]["id"] == "G1_1"
        assert data["ticket"]["title"] == "Test"
        assert data["ticket"]["description"] == "Test desc"
        assert data["criteria"][0]["criterion"] == "c1"

    def test_missing_ticket_errors(self, project):
        result = _tool_reset_ticket(project, {"ticket_id": "nope"})
        assert result.get("isError")
        assert "not found" in result["text"]

    def test_resets_closed_ticket(self, project):
        """Reset bypasses transition rules — can reset even closed tickets."""
        path = _write_ticket(project, status="closed", result="pass")
        result = _tool_reset_ticket(project, {"ticket_id": "G1_1"})
        assert not result.get("isError")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["ticket"]["status"] == "todo"
