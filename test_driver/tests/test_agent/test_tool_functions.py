"""Tests for internal functions in repo_tools.agent.tool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_tools.agent.tool import (
    _agent_run,
    _find_rules_file,
    _has_reviewable_changes,
)
from repo_tools.core import ToolContext, resolve_tokens


@pytest.fixture
def tool_ctx(tmp_path):
    ws = tmp_path / "project"
    ws.mkdir()
    dims = {"platform": "linux-x64", "build_type": "Debug"}
    tokens = resolve_tokens(str(ws), {}, dims)
    return ToolContext(
        workspace_root=ws,
        tokens=tokens,
        config={},
        tool_config={},
        dimensions=dims,
        passthrough_args=[],
    )


def _make_ticket(tool_ctx, ticket_id="G1_1", status="todo", criteria=None):
    """Helper to create a ticket JSON file for testing."""
    ticket_dir = tool_ctx.workspace_root / "_agent" / "tickets"
    ticket_dir.mkdir(parents=True, exist_ok=True)
    if criteria is None:
        criteria_list = []
    else:
        criteria_list = [{"criterion": c, "met": False} for c in criteria]
    data = {
        "ticket": {
            "id": ticket_id,
            "title": "Test ticket",
            "description": "Test description",
            "status": status,
        },
        "criteria": criteria_list,
        "progress": {"notes": ""},
        "review": {"result": "", "feedback": ""},
    }
    (ticket_dir / f"{ticket_id}.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


# ── _find_rules_file ──────────────────────────────────────────────


class TestFindRulesFile:
    def test_configured_rules_preferred(self, tool_ctx):
        project_rules = tool_ctx.workspace_root / "my_rules.toml"
        project_rules.write_text("# project rules", encoding="utf-8")

        result = _find_rules_file(tool_ctx.workspace_root, configured="my_rules.toml")
        assert result == project_rules

    def test_configured_missing_falls_back(self, tool_ctx):
        result = _find_rules_file(tool_ctx.workspace_root, configured="nonexistent.toml")
        assert result.name == "allowlist_default.toml"

    def test_falls_back_to_framework_default(self, tool_ctx):
        result = _find_rules_file(tool_ctx.workspace_root)
        assert result.name == "allowlist_default.toml"
        assert result.exists()


# ── Lifecycle gating ─────────────────────────────────────────────


class TestLifecycleGating:
    @patch("repo_tools.agent.tool.ensure_worktree")
    def test_worker_on_todo_proceeds(self, mock_wt, tool_ctx):
        """Worker on todo ticket should proceed (not exit)."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="todo")

        with patch("repo_tools.agent.tool.subprocess.run") as mock_run, \
             patch("repo_tools.agent.tool._backend") as mock_backend:
            mock_backend.build_command.return_value = ["claude", "-p", "test"]
            mock_run.return_value = MagicMock(
                stdout=json.dumps({
                    "type": "result", "subtype": "success",
                    "structured_output": {"ticket_id": "G1_1", "status": "in_progress", "notes": "started"},
                }),
                returncode=0,
            )
            _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})

    @patch("repo_tools.agent.tool.ensure_worktree")
    def test_worker_on_in_progress_proceeds(self, mock_wt, tool_ctx):
        """Worker on in_progress ticket should proceed (not exit)."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="in_progress")

        with patch("repo_tools.agent.tool.subprocess.run") as mock_run, \
             patch("repo_tools.agent.tool._backend") as mock_backend:
            mock_backend.build_command.return_value = ["claude", "-p", "test"]
            mock_run.return_value = MagicMock(
                stdout=json.dumps({
                    "type": "result", "subtype": "success",
                    "structured_output": {"ticket_id": "G1_1", "status": "verify", "notes": "done"},
                }),
                returncode=0,
            )
            _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})

    @patch("repo_tools.agent.tool.ensure_worktree")
    def test_worker_on_verify_exits(self, mock_wt, tool_ctx):
        """Worker on verify ticket should exit with error."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="verify")

        with pytest.raises(SystemExit):
            _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})

    @patch("repo_tools.agent.tool.ensure_worktree")
    def test_worker_on_closed_exits(self, mock_wt, tool_ctx):
        """Worker on closed ticket should exit with error."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="closed")

        with pytest.raises(SystemExit):
            _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})

    @patch("repo_tools.agent.tool.ensure_worktree")
    def test_reviewer_on_verify_proceeds(self, mock_wt, tool_ctx):
        """Reviewer on verify ticket should proceed (not exit)."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="verify")

        with patch("repo_tools.agent.tool.subprocess.run") as mock_run, \
             patch("repo_tools.agent.tool._backend") as mock_backend:
            mock_backend.build_command.return_value = ["claude", "-p", "test"]
            mock_run.return_value = MagicMock(
                stdout=json.dumps({
                    "type": "result", "subtype": "success",
                    "structured_output": {
                        "ticket_id": "G1_1", "status": "closed",
                        "result": "pass", "feedback": "All good",
                    },
                }),
                returncode=0,
            )
            _agent_run(tool_ctx, {"role": "reviewer", "ticket": "G1_1"})

    @patch("repo_tools.agent.tool.ensure_worktree")
    def test_reviewer_on_todo_exits(self, mock_wt, tool_ctx):
        """Reviewer on todo ticket should exit with error."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="todo")

        with pytest.raises(SystemExit):
            _agent_run(tool_ctx, {"role": "reviewer", "ticket": "G1_1"})

    @patch("repo_tools.agent.tool.ensure_worktree")
    def test_reviewer_on_in_progress_exits(self, mock_wt, tool_ctx):
        """Reviewer on in_progress ticket should exit with error."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="in_progress")

        with pytest.raises(SystemExit):
            _agent_run(tool_ctx, {"role": "reviewer", "ticket": "G1_1"})

    @patch("repo_tools.agent.tool.ensure_worktree")
    def test_reviewer_on_closed_exits(self, mock_wt, tool_ctx):
        """Reviewer on closed ticket should exit with error."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="closed")

        with pytest.raises(SystemExit):
            _agent_run(tool_ctx, {"role": "reviewer", "ticket": "G1_1"})

    @patch("repo_tools.agent.tool._has_reviewable_changes", return_value=False)
    @patch("repo_tools.agent.tool.ensure_worktree")
    def test_reviewer_exits_when_no_changes(self, mock_wt, _mock, tool_ctx):
        """Reviewer exits early when there are no reviewable changes."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="verify")

        with pytest.raises(SystemExit):
            _agent_run(tool_ctx, {"role": "reviewer", "ticket": "G1_1"})


# ── _has_reviewable_changes ──────────────────────────────────────


class TestHasReviewableChanges:
    @patch("repo_tools.agent.tool.subprocess.run")
    def test_uncommitted_changes_detected(self, mock_run):
        """Returns True when git diff HEAD shows changes."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _has_reviewable_changes(Path("/tmp/project")) is True

    @patch("repo_tools.agent.tool.subprocess.run")
    def test_untracked_files_detected(self, mock_run):
        """Returns True when untracked files exist."""
        def side_effect(cmd, **kwargs):
            if "diff" in cmd:
                return MagicMock(returncode=0)  # no diff
            if "ls-files" in cmd:
                return MagicMock(returncode=0, stdout="new_file.py\n")
            return MagicMock(returncode=1, stdout="")
        mock_run.side_effect = side_effect
        assert _has_reviewable_changes(Path("/tmp/project")) is True

    @patch("repo_tools.agent.tool.subprocess.run")
    def test_branch_diff_detected(self, mock_run):
        """Returns True when branch has commits ahead of main."""
        def side_effect(cmd, **kwargs):
            if "diff" in cmd:
                return MagicMock(returncode=0)
            if "ls-files" in cmd:
                return MagicMock(returncode=0, stdout="")
            if "log" in cmd and "main..HEAD" in cmd:
                return MagicMock(returncode=0, stdout="abc123 some commit\n")
            return MagicMock(returncode=1, stdout="")
        mock_run.side_effect = side_effect
        assert _has_reviewable_changes(Path("/tmp/project")) is True

    @patch("repo_tools.agent.tool.subprocess.run")
    def test_no_changes_returns_false(self, mock_run):
        """Returns False when there are no changes at all."""
        def side_effect(cmd, **kwargs):
            if "diff" in cmd:
                return MagicMock(returncode=0)  # clean
            if "ls-files" in cmd:
                return MagicMock(returncode=0, stdout="")  # no untracked
            if "log" in cmd:
                return MagicMock(returncode=0, stdout="")  # no branch diff
            return MagicMock(returncode=0, stdout="")
        mock_run.side_effect = side_effect
        assert _has_reviewable_changes(Path("/tmp/project")) is False


# ── _agent_run (headless mode) ───────────────────────────────────


def _claude_envelope(structured_output: dict) -> str:
    """Wrap structured output in Claude Code's --output-format json envelope."""
    return json.dumps({
        "type": "result", "subtype": "success",
        "structured_output": structured_output,
    })


class TestAgentRunHeadless:
    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_headless_updates_ticket(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """Headless mode parses structured output and writes it back to the ticket JSON."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx)

        mock_backend.build_command.return_value = [
            "claude", "-p", "prompt text", "--output-format", "json",
        ]
        mock_run.return_value = MagicMock(
            stdout=_claude_envelope({"ticket_id": "G1_1", "status": "in_progress", "notes": "implemented and tested"}),
            returncode=0,
        )

        result = _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})

        # Verify the result is returned
        parsed = json.loads(result)
        assert parsed["ticket_id"] == "G1_1"
        assert parsed["status"] == "in_progress"

        # Verify the ticket file was actually updated
        data = json.loads(
            (tool_ctx.workspace_root / "_agent" / "tickets" / "G1_1.json").read_text()
        )
        assert data["ticket"]["status"] == "in_progress"
        assert data["progress"]["notes"] == "implemented and tested"

    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_headless_reviewer_updates_ticket(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """Reviewer output updates status, result, feedback, and marks criteria."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="verify", criteria=["tests pass", "no lint errors"])

        mock_backend.build_command.return_value = ["claude", "-p", "test"]
        mock_run.return_value = MagicMock(
            stdout=_claude_envelope({
                "ticket_id": "G1_1", "status": "closed",
                "result": "pass", "feedback": "All tests passing",
                "criteria": [True, True],
            }),
            returncode=0,
        )

        _agent_run(tool_ctx, {"role": "reviewer", "ticket": "G1_1"})

        data = json.loads(
            (tool_ctx.workspace_root / "_agent" / "tickets" / "G1_1.json").read_text()
        )
        assert data["ticket"]["status"] == "closed"
        assert data["review"]["result"] == "pass"
        assert data["review"]["feedback"] == "All tests passing"
        assert all(c["met"] for c in data["criteria"])

    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_headless_reviewer_fail_records_partial_criteria(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """Reviewer fail marks met criteria and leaves unmet ones unchanged."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="verify", criteria=["A passes", "B passes", "C passes"])

        mock_backend.build_command.return_value = ["claude", "-p", "test"]
        mock_run.return_value = MagicMock(
            stdout=_claude_envelope({
                "ticket_id": "G1_1", "status": "todo",
                "result": "fail", "feedback": "B failed",
                "criteria": [True, False, True],
            }),
            returncode=0,
        )

        _agent_run(tool_ctx, {"role": "reviewer", "ticket": "G1_1"})

        data = json.loads(
            (tool_ctx.workspace_root / "_agent" / "tickets" / "G1_1.json").read_text()
        )
        assert data["ticket"]["status"] == "todo"
        assert data["criteria"][0]["met"] is True
        assert data["criteria"][1]["met"] is False
        assert data["criteria"][2]["met"] is True

    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_headless_reviewer_update_failure_returns_error(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """When ticket update fails, returned JSON contains an error key."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="verify")

        mock_backend.build_command.return_value = ["claude", "-p", "test"]
        mock_run.return_value = MagicMock(
            stdout=_claude_envelope({
                "ticket_id": "G1_1", "status": "closed",
                "result": "fail", "feedback": "contradictory",
                "criteria": [],
            }),
            returncode=0,
        )

        result = _agent_run(tool_ctx, {"role": "reviewer", "ticket": "G1_1"})

        parsed = json.loads(result)
        assert "error" in parsed

    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_headless_reads_ticket_json(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """Headless mode reads the ticket JSON and passes content in the prompt."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx)

        mock_backend.build_command.return_value = ["claude", "-p", "test"]
        mock_run.return_value = MagicMock(
            stdout=_claude_envelope({"ticket_id": "G1_1", "status": "in_progress", "notes": "ok"}),
            returncode=0,
        )

        _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})

        call_kwargs = mock_backend.build_command.call_args[1]
        assert call_kwargs["prompt"] is not None
        assert "G1_1" in call_kwargs["prompt"]
        assert "Test ticket" in call_kwargs["prompt"]

    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_headless_rejects_non_json(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """Non-JSON output is rejected — ticket is NOT updated."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx)

        mock_backend.build_command.return_value = ["claude", "-p", "test"]
        mock_run.return_value = MagicMock(stdout="plain text output", returncode=0)

        result = _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})
        assert result == "plain text output"

        # Ticket must remain unchanged
        data = json.loads(
            (tool_ctx.workspace_root / "_agent" / "tickets" / "G1_1.json").read_text()
        )
        assert data["ticket"]["status"] == "todo"

    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_headless_rejects_wrong_ticket_id(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """Output with wrong ticket_id is rejected — ticket is NOT updated."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx)

        mock_backend.build_command.return_value = ["claude", "-p", "test"]
        mock_run.return_value = MagicMock(
            stdout=_claude_envelope({"ticket_id": "WRONG", "status": "in_progress", "notes": "nope"}),
            returncode=0,
        )

        _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})

        data = json.loads(
            (tool_ctx.workspace_root / "_agent" / "tickets" / "G1_1.json").read_text()
        )
        assert data["ticket"]["status"] == "todo"

    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_headless_rejects_generic_error_envelope(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """Non-max-turns error envelope is rejected — ticket is NOT updated."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx)

        mock_backend.build_command.return_value = ["claude", "-p", "test"]
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"type": "result", "subtype": "error_other", "is_error": True}),
            returncode=0,
        )

        _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})

        data = json.loads(
            (tool_ctx.workspace_root / "_agent" / "tickets" / "G1_1.json").read_text()
        )
        assert data["ticket"]["status"] == "todo"

    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_error_max_turns_sets_in_progress(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """Worker hitting turn limit auto-transitions ticket to in_progress."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx)

        mock_backend.build_command.return_value = ["claude", "-p", "test"]
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True}),
            returncode=0,
        )

        _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})

        data = json.loads(
            (tool_ctx.workspace_root / "_agent" / "tickets" / "G1_1.json").read_text()
        )
        assert data["ticket"]["status"] == "in_progress"
        assert "turn limit" in data["progress"]["notes"]

    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_error_max_turns_reviewer_not_updated(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """Reviewer hitting turn limit does NOT auto-update the ticket."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx, status="verify")

        mock_backend.build_command.return_value = ["claude", "-p", "test"]
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True}),
            returncode=0,
        )

        _agent_run(tool_ctx, {"role": "reviewer", "ticket": "G1_1"})

        data = json.loads(
            (tool_ctx.workspace_root / "_agent" / "tickets" / "G1_1.json").read_text()
        )
        assert data["ticket"]["status"] == "verify"

    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_headless_rejects_missing_structured_output(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """Envelope without structured_output is rejected — ticket is NOT updated."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx)

        mock_backend.build_command.return_value = ["claude", "-p", "test"]
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"type": "result", "subtype": "success", "is_error": False}),
            returncode=0,
        )

        _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})

        data = json.loads(
            (tool_ctx.workspace_root / "_agent" / "tickets" / "G1_1.json").read_text()
        )
        assert data["ticket"]["status"] == "todo"

    @patch("repo_tools.agent.tool.ensure_worktree")
    @patch("repo_tools.agent.tool.subprocess.run")
    @patch("repo_tools.agent.tool._backend")
    def test_max_turns_from_config(self, mock_backend, mock_run, mock_wt, tool_ctx):
        """max_turns from tool_config is forwarded to build_command."""
        mock_wt.return_value = tool_ctx.workspace_root
        _make_ticket(tool_ctx)

        mock_backend.build_command.return_value = ["claude", "-p", "test"]
        mock_run.return_value = MagicMock(
            stdout=_claude_envelope({"ticket_id": "G1_1", "status": "in_progress", "notes": "ok"}),
            returncode=0,
        )

        _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1", "max_turns": 30})

        call_kwargs = mock_backend.build_command.call_args[1]
        assert call_kwargs["tool_config"]["max_turns"] == 30

    @patch("repo_tools.agent.tool.ensure_worktree")
    def test_headless_missing_ticket_exits(self, mock_wt, tool_ctx):
        """Headless mode exits if the ticket file doesn't exist."""
        mock_wt.return_value = tool_ctx.workspace_root
        with pytest.raises(SystemExit):
            _agent_run(tool_ctx, {"role": "worker", "ticket": "G1_1"})


# ── _agent_run (interactive mode) ────────────────────────────────


class TestAgentRunInteractive:
    @patch("repo_tools.agent.tool.sys.exit", side_effect=SystemExit(0))
    @patch("repo_tools.agent.tool.subprocess.run", return_value=MagicMock(returncode=0))
    @patch("repo_tools.agent.tool._backend")
    def test_interactive_launches_command(self, mock_backend, mock_run, mock_exit, tool_ctx):
        """Interactive mode (no ticket) launches the agent command."""
        mock_backend.build_command.return_value = ["claude", "--allowedTools", "Read"]

        with pytest.raises(SystemExit):
            _agent_run(tool_ctx, {})

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" not in cmd

    @patch("repo_tools.agent.tool.sys.exit", side_effect=SystemExit(0))
    @patch("repo_tools.agent.tool.subprocess.run", return_value=MagicMock(returncode=0))
    @patch("repo_tools.agent.tool._backend")
    def test_interactive_no_prompt_flag(self, mock_backend, mock_run, mock_exit, tool_ctx):
        """Interactive mode does not include -p or --output-format."""
        mock_backend.build_command.return_value = ["claude", "--allowedTools", "Read"]

        with pytest.raises(SystemExit):
            _agent_run(tool_ctx, {})

        cmd = mock_run.call_args[0][0]
        assert "--output-format" not in cmd
