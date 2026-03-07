"""Tests for repo_tools.agent.dispatch — the dispatch_agent MCP tool handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_tools.agent.dispatch import TOOL_SCHEMA, call_dispatch


class TestToolSchema:
    def test_schema_has_required_fields(self):
        assert TOOL_SCHEMA["name"] == "dispatch_agent"
        assert "inputSchema" in TOOL_SCHEMA
        props = TOOL_SCHEMA["inputSchema"]["properties"]
        assert "ticket_id" in props
        assert "role" in props
        assert props["role"]["enum"] == ["worker", "reviewer"]

    def test_schema_has_optional_branch_and_project_dir(self):
        props = TOOL_SCHEMA["inputSchema"]["properties"]
        assert "branch" in props
        assert props["branch"]["type"] == "string"
        assert "project_dir" in props
        assert props["project_dir"]["type"] == "string"

    def test_schema_requires_only_ticket_and_role(self):
        assert set(TOOL_SCHEMA["inputSchema"]["required"]) == {"ticket_id", "role"}


class TestCallDispatchValidation:
    def test_invalid_role_returns_error(self):
        result = call_dispatch(
            {"role": "admin", "ticket_id": "t1"},
            workspace_root=Path("/tmp"),
        )
        assert result["isError"] is True
        assert "Invalid role" in result["text"]

    def test_empty_role_returns_error(self):
        result = call_dispatch(
            {"role": "", "ticket_id": "t1"},
            workspace_root=Path("/tmp"),
        )
        assert result["isError"] is True

    def test_missing_ticket_id_returns_error(self):
        result = call_dispatch(
            {"role": "worker", "ticket_id": ""},
            workspace_root=Path("/tmp"),
        )
        assert result["isError"] is True
        assert "ticket_id" in result["text"]

    def test_missing_role_key_returns_error(self):
        result = call_dispatch(
            {"ticket_id": "t1"},
            workspace_root=Path("/tmp"),
        )
        assert result["isError"] is True

    def test_missing_ticket_key_returns_error(self):
        result = call_dispatch(
            {"role": "worker"},
            workspace_root=Path("/tmp"),
        )
        assert result["isError"] is True


class TestCallDispatchSubprocess:
    @patch("repo_tools.agent.dispatch.subprocess.run")
    def test_success_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"ticket_id": "t1", "status": "verify", "notes": "done"}',
            stderr="",
        )
        result = call_dispatch(
            {"role": "worker", "ticket_id": "t1"},
            workspace_root=Path("/tmp/project"),
        )
        assert result.get("isError") is None or result.get("isError") is False
        assert "t1" in result["text"]

    @patch("repo_tools.agent.dispatch.subprocess.run")
    def test_success_passes_correct_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        call_dispatch(
            {"role": "reviewer", "ticket_id": "fix-bug"},
            workspace_root=Path("/tmp/project"),
        )
        cmd = mock_run.call_args[0][0]
        assert "--role" in cmd
        assert "reviewer" in cmd
        assert "--ticket" in cmd
        assert "fix-bug" in cmd
        assert "--workspace-root" in cmd

    @patch("repo_tools.agent.dispatch.subprocess.run")
    def test_branch_passed_to_subprocess(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        call_dispatch(
            {"role": "worker", "ticket_id": "t1", "branch": "main"},
            workspace_root=Path("/tmp/project"),
        )
        cmd = mock_run.call_args[0][0]
        assert "--branch" in cmd
        idx = cmd.index("--branch")
        assert cmd[idx + 1] == "main"

    @patch("repo_tools.agent.dispatch.subprocess.run")
    def test_branch_omitted_when_not_provided(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        call_dispatch(
            {"role": "worker", "ticket_id": "t1"},
            workspace_root=Path("/tmp/project"),
        )
        cmd = mock_run.call_args[0][0]
        assert "--branch" not in cmd

    @patch("repo_tools.agent.dispatch.subprocess.run")
    def test_project_dir_overrides_workspace_root(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        call_dispatch(
            {"role": "worker", "ticket_id": "t1", "project_dir": "/other/project"},
            workspace_root=Path("/tmp/project"),
        )
        cmd = mock_run.call_args[0][0]
        ws_idx = cmd.index("--workspace-root")
        assert cmd[ws_idx + 1] == str(Path("/other/project"))

    @patch("repo_tools.agent.dispatch.subprocess.run")
    def test_nonzero_exit_returns_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="ticket not found",
        )
        result = call_dispatch(
            {"role": "worker", "ticket_id": "missing"},
            workspace_root=Path("/tmp/project"),
        )
        assert result["isError"] is True
        assert "ticket not found" in result["text"]

    @patch("repo_tools.agent.dispatch.subprocess.run")
    def test_nonzero_exit_with_stdout_only(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="error details on stdout",
            stderr="",
        )
        result = call_dispatch(
            {"role": "worker", "ticket_id": "t1"},
            workspace_root=Path("/tmp/project"),
        )
        assert result["isError"] is True
        assert "error details" in result["text"]

    @patch("repo_tools.agent.dispatch.subprocess.run")
    def test_nonzero_exit_no_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=42, stdout="", stderr="",
        )
        result = call_dispatch(
            {"role": "worker", "ticket_id": "t1"},
            workspace_root=Path("/tmp/project"),
        )
        assert result["isError"] is True
        assert "42" in result["text"]

    @patch("repo_tools.agent.dispatch.subprocess.run", side_effect=OSError("no such file"))
    def test_os_error_returns_error(self, mock_run):
        result = call_dispatch(
            {"role": "worker", "ticket_id": "t1"},
            workspace_root=Path("/tmp/project"),
        )
        assert result["isError"] is True
        assert "Failed to launch" in result["text"]

    @patch("repo_tools.agent.dispatch.subprocess.run")
    def test_empty_stdout_on_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = call_dispatch(
            {"role": "worker", "ticket_id": "t1"},
            workspace_root=Path("/tmp/project"),
        )
        assert result["text"] == "Dispatch completed."
