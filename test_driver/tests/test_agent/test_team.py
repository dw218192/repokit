"""Tests for TeamManager in repo_tools.agent.team."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_tools.agent.team import TeamManager
from repo_tools.core import ToolContext, resolve_tokens


@pytest.fixture
def team_ctx(tmp_path):
    """Create a ToolContext for team tests."""
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


class TestTeamStart:
    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team._agent_run")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_creates_directory_structure(self, mock_ensure, mock_run, mock_server_cls, team_ctx):
        mock_server_cls.return_value.run.return_value = None
        mgr = TeamManager(team_ctx)
        mgr.start("test1")

        ws_dir = team_ctx.workspace_root / "_agent" / "test1"
        assert ws_dir.exists()
        assert (ws_dir / "tickets").exists()
        assert (ws_dir / "plan.toml").exists()

    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team._agent_run")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_creates_default_plan(self, mock_ensure, mock_run, mock_server_cls, team_ctx):
        mock_server_cls.return_value.run.return_value = None
        mgr = TeamManager(team_ctx)
        mgr.start("ws1")

        plan = (team_ctx.workspace_root / "_agent" / "ws1" / "plan.toml").read_text()
        assert 'id = "ws1"' in plan

    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team._agent_run")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_spawns_orchestrator(self, mock_ensure, mock_run, mock_server_cls, team_ctx):
        """Orchestrator is spawned before the MCP server blocks."""
        mock_server_cls.return_value.run.return_value = None
        mgr = TeamManager(team_ctx)
        mgr.start("ws3")

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["role"] == "orchestrator"
        assert call_kwargs["workstream"] == "ws3"

    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team._agent_run")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_writes_mcp_port_file(self, mock_ensure, mock_run, mock_server_cls, team_ctx):
        """mcp.port is written so _agent_run can read it for worker registration."""
        mock_server_cls.return_value.run.return_value = None
        mgr = TeamManager(team_ctx)
        mgr.start("ws4")

        port_file = team_ctx.workspace_root / "_agent" / "ws4" / "mcp.port"
        assert port_file.exists()
        port = int(port_file.read_text().strip())
        assert 1024 <= port <= 65535

    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team._agent_run")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_starts_mcp_server(self, mock_ensure, mock_run, mock_server_cls, team_ctx):
        """MCP server is started (and blocks) after the orchestrator is spawned."""
        mock_server = MagicMock()
        mock_server_cls.return_value = mock_server
        mgr = TeamManager(team_ctx)
        mgr.start("ws5")

        mock_server.run.assert_called_once()

    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team._agent_run")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_resumes_existing_workstream(self, mock_ensure, mock_run, mock_server_cls, team_ctx):
        """start() on an existing workstream resumes — spawns orchestrator and MCP server."""
        mock_server_cls.return_value.run.return_value = None
        ws_dir = team_ctx.workspace_root / "_agent" / "resume1"
        ws_dir.mkdir(parents=True)

        mgr = TeamManager(team_ctx)
        mgr.start("resume1")

        mock_run.assert_called_once()
        mock_server_cls.return_value.run.assert_called_once()


class TestTeamConfig:
    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team._agent_run")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_custom_idle_config_passed_to_server(self, mock_ensure, mock_run, mock_server_cls, tmp_path):
        """idle_reminder_interval and idle_reminder_limit from tool_config reach the server."""
        mock_server_cls.return_value.run.return_value = None
        ws = tmp_path / "project"
        ws.mkdir()
        dims = {"platform": "linux-x64", "build_type": "Debug"}
        tokens = resolve_tokens(str(ws), {}, dims)
        ctx = ToolContext(
            workspace_root=ws,
            tokens=tokens,
            config={},
            tool_config={"idle_reminder_interval": 60, "idle_reminder_limit": 5},
            dimensions=dims,
            passthrough_args=[],
        )
        mgr = TeamManager(ctx)
        mgr.start("ws_cfg")

        args, kwargs = mock_server_cls.call_args
        # TeamMCPServer(workstream, port, interval, limit)
        assert args[2] == 60
        assert args[3] == 5


class TestRenderRolePrompt:
    def test_render_orchestrator(self):
        from repo_tools.agent.tool import _render_role_prompt
        text = _render_role_prompt(
            "orchestrator",
            workstream_id="ws1",
            ticket_id="",
            worktree_path="/tmp/work",
            ticket_path="",
            branch="agent/ws1",
            project_root="/tmp/project",
            repo_cmd="./repo",
            framework_root="/tmp/framework",
        )
        assert "ws1" in text
        assert "orchestrator" in text.lower() or "ORCHESTRATOR" in text

    def test_render_worker(self):
        from repo_tools.agent.tool import _render_role_prompt
        text = _render_role_prompt(
            "worker",
            workstream_id="ws1",
            ticket_id="G1_1",
            worktree_path="/tmp/work/G1_1",
            ticket_path="/tmp/project/_agent/ws1/tickets/G1_1.toml",
            branch="agent/ws1/G1_1",
            project_root="/tmp/project",
            repo_cmd="./repo",
            framework_root="/tmp/framework",
        )
        assert "G1_1" in text
        assert "worker" in text.lower() or "WORKER" in text

    def test_render_reviewer(self):
        from repo_tools.agent.tool import _render_role_prompt
        text = _render_role_prompt(
            "reviewer",
            workstream_id="ws1",
            ticket_id="G1_1",
            worktree_path="/tmp/work/G1_1",
            ticket_path="/tmp/project/_agent/ws1/tickets/G1_1.toml",
            branch="agent/ws1/G1_1",
            project_root="/tmp/project",
            repo_cmd="./repo",
            framework_root="/tmp/framework",
        )
        assert "G1_1" in text
        assert "reviewer" in text.lower() or "REVIEWER" in text

    def test_render_nonexistent_role(self):
        from repo_tools.agent.tool import _render_role_prompt
        text = _render_role_prompt(
            "nonexistent",
            workstream_id="ws1",
            ticket_id="",
            worktree_path="",
            ticket_path="",
            branch="",
            project_root="",
            repo_cmd="",
            framework_root="",
        )
        assert text == ""

    def test_orchestrator_does_not_use_auto_approve(self):
        from repo_tools.agent.tool import _render_role_prompt
        text = _render_role_prompt(
            "orchestrator",
            workstream_id="ws1",
            ticket_id="",
            worktree_path="/tmp/work",
            ticket_path="",
            branch="agent/ws1",
            project_root="/tmp/project",
            repo_cmd="./repo",
            framework_root="/tmp/framework",
        )
        assert "--auto-approve" not in text

    def test_worker_uses_send_message_tool(self):
        from repo_tools.agent.tool import _render_role_prompt
        text = _render_role_prompt(
            "worker",
            workstream_id="ws1",
            ticket_id="G1_1",
            worktree_path="/tmp/work/G1_1",
            ticket_path="/tmp/project/_agent/ws1/tickets/G1_1.toml",
            branch="agent/ws1/G1_1",
            project_root="/tmp/project",
            repo_cmd="./repo",
            framework_root="/tmp/framework",
        )
        assert "send_message" in text
        assert "status=verify" in text
        assert "_agent/" in text and ("not" in text.lower() or "do not" in text.lower())

    def test_reviewer_uses_send_message_tool(self):
        from repo_tools.agent.tool import _render_role_prompt
        text = _render_role_prompt(
            "reviewer",
            workstream_id="ws1",
            ticket_id="G1_1",
            worktree_path="/tmp/work/G1_1",
            ticket_path="/tmp/project/_agent/ws1/tickets/G1_1.toml",
            branch="agent/ws1/G1_1",
            project_root="/tmp/project",
            repo_cmd="./repo",
            framework_root="/tmp/framework",
        )
        assert "send_message" in text
        assert "status=closed" in text
        assert "status=open" in text
        assert "_agent/" in text and ("not" in text.lower() or "do not" in text.lower())
