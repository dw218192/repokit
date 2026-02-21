"""TeamManager — orchestrate multi-agent workstreams."""

from __future__ import annotations

import re
from pathlib import Path

from ..core import ToolContext, logger
from .mcp_server import DEFAULT_REMINDER_INTERVAL, DEFAULT_REMINDER_LIMIT, TeamMCPServer, find_free_port
from .wezterm import ensure_installed
from .tool import _agent_run


class TeamManager:
    """High-level operations for multi-agent workstreams.

    Workstream layout::

        _agent/<workstream_id>/
            plan.toml
            mcp.port          ← MCP server port (written at session start)
            settings.json     ← per-workstream Claude Code settings
            tickets/
                G1_1.toml
                ...
            worktrees/
                ...
    """

    def __init__(self, ctx: ToolContext) -> None:
        self._ctx = ctx
        self._root = ctx.workspace_root

    def _ws_dir(self, workstream_id: str) -> Path:
        return self._root / "_agent" / workstream_id

    _SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

    def start(self, workstream_id: str) -> None:
        """Start a workstream — creates dirs if new, then spawns orchestrator + MCP server."""
        ensure_installed()
        if not self._SAFE_ID_RE.match(workstream_id):
            raise ValueError(
                f"workstream_id must contain only alphanumerics, hyphens, and underscores; "
                f"got {workstream_id!r}"
            )

        ws_dir = self._ws_dir(workstream_id)

        if ws_dir.exists():
            logger.info(f"Resuming workstream '{workstream_id}' at {ws_dir}")
        else:
            ws_dir.mkdir(parents=True)
            (ws_dir / "tickets").mkdir()
            (ws_dir / "worktrees").mkdir()
            (ws_dir / "plan.toml").write_text(
                f'[workstream]\nid = "{workstream_id}"\ndescription = ""\n\n'
                f'[[goal]]\nid = "G1"\ndescription = ""\n\n'
                f'[[acceptance]]\ncriterion = ""\n',
                encoding="utf-8",
            )
            logger.info(f"Workstream '{workstream_id}' created at {ws_dir}")

        port = find_free_port()
        (ws_dir / "mcp.port").write_text(str(port), encoding="utf-8")

        _agent_run(
            self._ctx,
            workspace=workstream_id,
            role="orchestrator",
            workstream=workstream_id,
            mcp_port=port,
        )

        server = self._make_server(workstream_id, port)
        server.run()

    # ── Internal ─────────────────────────────────────────────────────────

    def _make_server(self, workstream_id: str, port: int) -> TeamMCPServer:
        interval = int(
            self._ctx.tool_config.get("idle_reminder_interval", DEFAULT_REMINDER_INTERVAL)
        )
        limit = int(
            self._ctx.tool_config.get("idle_reminder_limit", DEFAULT_REMINDER_LIMIT)
        )
        return TeamMCPServer(workstream_id, port, interval, limit)
