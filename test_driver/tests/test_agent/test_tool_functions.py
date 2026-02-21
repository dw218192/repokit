"""Tests for internal functions in repo_tools.agent.tool."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from repo_tools.agent.tool import (
    _agent_run,
    _find_rules_file,
    _register_pane,
    _setup_worktree,
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


# ── _setup_worktree ───────────────────────────────────────────────


class TestSetupWorktree:
    def test_existing_worktree_returns_immediately(self, tool_ctx):
        wt = tool_ctx.workspace_root / "_agent" / "ws1" / "worktrees" / "G1_1"
        wt.mkdir(parents=True)

        result = _setup_worktree(tool_ctx.workspace_root, "ws1", "G1_1")
        assert result == wt

    @patch("repo_tools.agent.tool.subprocess.run")
    def test_creates_new_worktree(self, mock_run, tool_ctx):
        mock_run.return_value = MagicMock(returncode=0)

        result = _setup_worktree(tool_ctx.workspace_root, "ws1", "G1_1")
        assert result == tool_ctx.workspace_root / "_agent" / "ws1" / "worktrees" / "G1_1"

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "git" in args
        assert "worktree" in args
        assert "add" in args
        assert "-b" in args
        assert "agent/ws1/G1_1" in args

    @patch("repo_tools.agent.tool.subprocess.run")
    def test_branch_already_exists_fallback(self, mock_run, tool_ctx):
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "git"),
            MagicMock(returncode=0),
        ]

        result = _setup_worktree(tool_ctx.workspace_root, "ws1", "G1_1")
        assert result == tool_ctx.workspace_root / "_agent" / "ws1" / "worktrees" / "G1_1"
        assert mock_run.call_count == 2

        second_args = mock_run.call_args_list[1][0][0]
        assert "-b" not in second_args


# ── _register_pane ────────────────────────────────────────────────


class TestRegisterPane:
    @patch("repo_tools.agent.tool.urllib.request.urlopen")
    def test_posts_to_mcp_server(self, mock_urlopen, tool_ctx):
        _register_pane(18042, 7, "worker", "ws1", "G1_1")
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert "18042" in req.full_url
        assert b"G1_1" in req.data

    @patch("repo_tools.agent.tool.urllib.request.urlopen", side_effect=OSError("refused"))
    def test_silently_ignores_failure(self, mock_urlopen, tool_ctx):
        # Should not raise
        _register_pane(18042, 7, "worker", "ws1", "G1_1")


# ── _agent_run (solo mode) ────────────────────────────────────────


class TestAgentRunSolo:
    @patch("repo_tools.agent.tool.activate_pane")
    @patch("repo_tools.agent.tool.PaneSession")
    @patch("repo_tools.agent.tool.spawn_in_workspace")
    @patch("repo_tools.agent.tool._backend")
    @patch("repo_tools.agent.tool.ensure_installed")
    def test_solo_spawns_and_returns(self, mock_ensure, mock_backend, mock_spawn, mock_pane_cls, mock_activate, tool_ctx):
        session = MagicMock()
        session.pane_id = 42
        mock_pane_cls.spawn.return_value = session
        mock_backend.build_command.return_value = ["claude", "--allowedTools", "Edit"]

        _agent_run(tool_ctx)

        mock_pane_cls.spawn.assert_called_once()
        session.is_alive.assert_not_called()

    @patch("repo_tools.agent.tool.activate_pane")
    @patch("repo_tools.agent.tool.PaneSession")
    @patch("repo_tools.agent.tool.spawn_in_workspace")
    @patch("repo_tools.agent.tool._backend")
    @patch("repo_tools.agent.tool.ensure_installed")
    def test_solo_with_workspace_uses_spawn_in_workspace(self, mock_ensure, mock_backend, mock_spawn, mock_pane_cls, mock_activate, tool_ctx):
        session = MagicMock()
        session.pane_id = 10
        mock_spawn.return_value = session
        mock_backend.build_command.return_value = ["claude"]

        _agent_run(tool_ctx, workspace="my-ws")

        mock_spawn.assert_called_once()
        assert mock_spawn.call_args[0][1] == "my-ws"

    @patch("repo_tools.agent.tool.activate_pane")
    @patch("repo_tools.agent.tool.PaneSession")
    @patch("repo_tools.agent.tool._backend")
    @patch("repo_tools.agent.tool.ensure_installed")
    def test_solo_spawn_failure_exits(self, mock_ensure, mock_backend, mock_pane_cls, mock_activate, tool_ctx):
        mock_pane_cls.spawn.return_value = None
        mock_backend.build_command.return_value = ["claude"]

        with pytest.raises(SystemExit):
            _agent_run(tool_ctx)


# ── _agent_run (team mode) ────────────────────────────────────────


class TestAgentRunTeam:
    @patch("repo_tools.agent.tool.activate_pane")
    @patch("repo_tools.agent.tool._setup_worktree")
    @patch("repo_tools.agent.tool._register_pane")
    @patch("repo_tools.agent.tool.spawn_in_workspace")
    @patch("repo_tools.agent.tool._backend")
    @patch("repo_tools.agent.tool.ensure_installed")
    def test_spawns_and_returns_immediately(self, mock_ensure, mock_backend, mock_spawn, mock_register, mock_worktree, mock_activate, tool_ctx):
        """_agent_run spawns the pane and returns without blocking."""
        mock_worktree.return_value = tool_ctx.workspace_root / "wt"
        session = MagicMock()
        session.pane_id = 42
        mock_spawn.return_value = session
        mock_backend.build_command.return_value = ["claude"]

        _agent_run(tool_ctx, workspace="ws1", role="worker", workstream="ws1", ticket="G1_1")

        mock_spawn.assert_called_once()
        session.is_alive.assert_not_called()
        session.kill.assert_not_called()

    @patch("repo_tools.agent.tool.activate_pane")
    @patch("repo_tools.agent.tool._setup_worktree")
    @patch("repo_tools.agent.tool._register_pane")
    @patch("repo_tools.agent.tool.spawn_in_workspace")
    @patch("repo_tools.agent.tool._backend")
    @patch("repo_tools.agent.tool.ensure_installed")
    def test_worker_registers_pane_with_mcp_server(self, mock_ensure, mock_backend, mock_spawn, mock_register, mock_worktree, mock_activate, tool_ctx):
        """Worker panes are registered with the MCP server after spawn."""
        mock_worktree.return_value = tool_ctx.workspace_root / "wt"
        session = MagicMock()
        session.pane_id = 99
        mock_spawn.return_value = session
        mock_backend.build_command.return_value = ["claude"]

        _agent_run(
            tool_ctx, workspace="ws1", role="worker", workstream="ws1",
            ticket="G1_1", mcp_port=18042,
        )

        mock_register.assert_called_once_with(18042, 99, "worker", "ws1", "G1_1")

    @patch("repo_tools.agent.tool.activate_pane")
    @patch("repo_tools.agent.tool._setup_worktree")
    @patch("repo_tools.agent.tool._register_pane")
    @patch("repo_tools.agent.tool.spawn_in_workspace")
    @patch("repo_tools.agent.tool._backend")
    @patch("repo_tools.agent.tool.ensure_installed")
    def test_orchestrator_not_registered(self, mock_ensure, mock_backend, mock_spawn, mock_register, mock_worktree, mock_activate, tool_ctx):
        """Orchestrator panes are NOT registered (not subject to idle kill)."""
        session = MagicMock()
        session.pane_id = 5
        session.is_alive.return_value = False
        mock_spawn.return_value = session
        mock_backend.build_command.return_value = ["claude"]

        _agent_run(
            tool_ctx, workspace="ws1", role="orchestrator", workstream="ws1",
            mcp_port=18042,
        )

        mock_register.assert_not_called()

    @patch("repo_tools.agent.tool.activate_pane")
    @patch("repo_tools.agent.tool.spawn_in_workspace")
    @patch("repo_tools.agent.tool._backend")
    @patch("repo_tools.agent.tool.ensure_installed")
    def test_team_renders_role_prompt(self, mock_ensure, mock_backend, mock_spawn, mock_activate, tool_ctx):
        session = MagicMock()
        session.pane_id = 42
        session.is_alive.return_value = False
        mock_spawn.return_value = session
        mock_backend.build_command.return_value = ["claude"]

        _agent_run(tool_ctx, workspace="ws1", role="orchestrator", workstream="ws1")

        call_kwargs = mock_backend.build_command.call_args[1]
        assert call_kwargs["role"] == "orchestrator"
        assert call_kwargs["role_prompt"] is not None

    @patch("repo_tools.agent.tool.activate_pane")
    @patch("repo_tools.agent.tool._setup_worktree")
    @patch("repo_tools.agent.tool._register_pane")
    @patch("repo_tools.agent.tool.spawn_in_workspace")
    @patch("repo_tools.agent.tool._backend")
    @patch("repo_tools.agent.tool.ensure_installed")
    def test_mcp_port_read_from_port_file(self, mock_ensure, mock_backend, mock_spawn, mock_register, mock_worktree, mock_activate, tool_ctx):
        """When mcp_port not given, _agent_run reads it from _agent/{ws}/mcp.port."""
        mock_worktree.return_value = tool_ctx.workspace_root / "wt"
        port_file = tool_ctx.workspace_root / "_agent" / "ws1" / "mcp.port"
        port_file.parent.mkdir(parents=True, exist_ok=True)
        port_file.write_text("19999", encoding="utf-8")

        session = MagicMock()
        session.pane_id = 7
        mock_spawn.return_value = session
        mock_backend.build_command.return_value = ["claude"]

        _agent_run(
            tool_ctx, workspace="ws1", role="worker",
            workstream="ws1", ticket="G1_1",
        )

        mock_register.assert_called_once_with(19999, 7, "worker", "ws1", "G1_1")
