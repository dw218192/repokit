"""Tests for TeamManager in repo_tools.agent.team."""

from __future__ import annotations

import textwrap
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


class TestTeamNew:
    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team._agent_run")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_creates_directory_structure(self, mock_ensure, mock_run, mock_server_cls, team_ctx):
        mock_server_cls.return_value.run.return_value = None
        mgr = TeamManager(team_ctx)
        mgr.new("test1")

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
        mgr.new("ws1")

        plan = (team_ctx.workspace_root / "_agent" / "ws1" / "plan.toml").read_text()
        assert 'id = "ws1"' in plan

    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team._agent_run")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_copies_plan_file(self, mock_ensure, mock_run, mock_server_cls, team_ctx):
        mock_server_cls.return_value.run.return_value = None
        plan_src = team_ctx.workspace_root / "my_plan.toml"
        plan_src.write_text('[workstream]\nid = "custom"\n', encoding="utf-8")

        mgr = TeamManager(team_ctx)
        mgr.new("ws2", plan_path=str(plan_src))

        plan = (team_ctx.workspace_root / "_agent" / "ws2" / "plan.toml").read_text()
        assert 'id = "custom"' in plan

    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team._agent_run")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_spawns_orchestrator(self, mock_ensure, mock_run, mock_server_cls, team_ctx):
        """Orchestrator is spawned before the MCP server blocks."""
        mock_server_cls.return_value.run.return_value = None
        mgr = TeamManager(team_ctx)
        mgr.new("ws3")

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
        mgr.new("ws4")

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
        mgr.new("ws5")

        mock_server.run.assert_called_once()

    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team._agent_run")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_duplicate_workstream_error(self, mock_ensure, mock_run, mock_server_cls, team_ctx, capsys):
        ws_dir = team_ctx.workspace_root / "_agent" / "dup"
        ws_dir.mkdir(parents=True)

        mgr = TeamManager(team_ctx)
        mgr.new("dup")

        mock_run.assert_not_called()
        mock_server_cls.return_value.run.assert_not_called()


class TestTeamAttach:
    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_attach_starts_mcp_server(self, mock_ensure, mock_server_cls, team_ctx):
        """attach() starts the MCP server (blocks) without spawning a new orchestrator."""
        mock_server = MagicMock()
        mock_server_cls.return_value = mock_server
        ws_dir = team_ctx.workspace_root / "_agent" / "ws1"
        ws_dir.mkdir(parents=True)
        (ws_dir / "mcp.port").write_text("18042", encoding="utf-8")

        mgr = TeamManager(team_ctx)
        mgr.attach("ws1")

        mock_server.run.assert_called_once()

    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_attach_uses_existing_port(self, mock_ensure, mock_server_cls, team_ctx):
        mock_server_cls.return_value.run.return_value = None
        ws_dir = team_ctx.workspace_root / "_agent" / "ws1"
        ws_dir.mkdir(parents=True)
        (ws_dir / "mcp.port").write_text("19876", encoding="utf-8")

        mgr = TeamManager(team_ctx)
        mgr.attach("ws1")

        # Server was constructed with the port from the file
        args = mock_server_cls.call_args
        assert args[0][1] == 19876  # positional: (workstream, port, ...)

    @patch("repo_tools.agent.team.TeamMCPServer")
    @patch("repo_tools.agent.team.ensure_installed")
    def test_attach_nonexistent_error(self, mock_ensure, mock_server_cls, team_ctx):
        mgr = TeamManager(team_ctx)
        mgr.attach("nonexistent")
        mock_server_cls.return_value.run.assert_not_called()


class TestTeamStatus:
    @patch("repo_tools.agent.team.ensure_installed")
    @patch("repo_tools.agent.team.list_workspace")
    def test_status_with_tickets(self, mock_list, mock_ensure, team_ctx, capsys):
        ws_dir = team_ctx.workspace_root / "_agent" / "ws1"
        tickets_dir = ws_dir / "tickets"
        tickets_dir.mkdir(parents=True)
        (tickets_dir / "G1_1.toml").write_text(
            textwrap.dedent("""\
            [ticket]
            id = "G1_1"
            title = "Implement feature A"
            status = "verify"
            """),
            encoding="utf-8",
        )
        mock_list.return_value = [{"pane_id": 10, "title": "orchestrator"}]

        mgr = TeamManager(team_ctx)
        mgr.status("ws1")

        captured = capsys.readouterr()
        assert "ws1" in captured.out
        assert "G1_1" in captured.out
        assert "verify" in captured.out
        assert "Implement feature A" in captured.out


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
        mgr.new("ws_cfg")

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
        )
        assert "send_message" in text
        assert "status=closed" in text
        assert "status=open" in text
        assert "_agent/" in text and ("not" in text.lower() or "do not" in text.lower())
