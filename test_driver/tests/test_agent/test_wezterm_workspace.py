"""Tests for WezTerm workspace primitives in repo_tools.agent.wezterm."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from repo_tools.agent.wezterm import (
    PaneSession,
    kill_workspace,
    list_workspace,
    spawn_in_workspace,
)


def _mock_run_cli(returncode=0, stdout="", stderr=""):
    """Create a mock for _run_cli that returns a CompletedProcess-like object."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


class TestSpawnInWorkspace:
    @patch("repo_tools.agent.wezterm._run_cli")
    def test_spawn_success(self, mock_cli):
        mock_cli.return_value = _mock_run_cli(stdout="42\n")
        session = spawn_in_workspace(["echo", "hi"], "test-ws", cwd="/tmp")
        assert session is not None
        assert session.pane_id == 42
        args = mock_cli.call_args[0]
        assert "spawn" in args
        assert "--workspace" in args
        assert "test-ws" in args
        assert "--cwd" in args
        assert "/tmp" in args

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_spawn_without_cwd(self, mock_cli):
        mock_cli.return_value = _mock_run_cli(stdout="10\n")
        session = spawn_in_workspace(["cmd"], "ws1")
        assert session is not None
        assert session.pane_id == 10
        args = mock_cli.call_args[0]
        assert "--cwd" not in args

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_spawn_failure(self, mock_cli):
        mock_cli.return_value = _mock_run_cli(returncode=1, stderr="error")
        session = spawn_in_workspace(["cmd"], "ws1")
        assert session is None

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_spawn_bad_output(self, mock_cli):
        mock_cli.return_value = _mock_run_cli(stdout="not-a-number")
        session = spawn_in_workspace(["cmd"], "ws1")
        assert session is None


class TestListWorkspace:
    @patch("repo_tools.agent.wezterm._run_cli")
    def test_list_filters_by_workspace(self, mock_cli):
        panes = [
            {"pane_id": 1, "workspace": "ws1"},
            {"pane_id": 2, "workspace": "ws2"},
            {"pane_id": 3, "workspace": "ws1"},
        ]
        mock_cli.return_value = _mock_run_cli(stdout=json.dumps(panes))
        result = list_workspace("ws1")
        assert len(result) == 2
        assert all(p["workspace"] == "ws1" for p in result)

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_list_empty_workspace(self, mock_cli):
        mock_cli.return_value = _mock_run_cli(stdout=json.dumps([]))
        result = list_workspace("nonexistent")
        assert result == []

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_list_cli_error(self, mock_cli):
        mock_cli.return_value = _mock_run_cli(returncode=1)
        result = list_workspace("ws1")
        assert result == []


class TestKillWorkspace:
    @patch("repo_tools.agent.wezterm._run_cli")
    def test_kill_all_panes(self, mock_cli):
        panes = [
            {"pane_id": 1, "workspace": "ws1"},
            {"pane_id": 3, "workspace": "ws1"},
        ]
        # First call: list; subsequent calls: kill-pane
        mock_cli.side_effect = [
            _mock_run_cli(stdout=json.dumps(panes)),  # list
            _mock_run_cli(),  # kill 1
            _mock_run_cli(),  # kill 3
        ]
        count = kill_workspace("ws1")
        assert count == 2

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_kill_empty_workspace(self, mock_cli):
        mock_cli.return_value = _mock_run_cli(stdout=json.dumps([]))
        count = kill_workspace("empty")
        assert count == 0
