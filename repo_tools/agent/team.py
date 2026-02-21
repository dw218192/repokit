"""TeamManager — orchestrate multi-agent workstreams."""

from __future__ import annotations

import re
import shutil

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

from pathlib import Path

from ..core import ToolContext, logger
from .mcp_server import DEFAULT_REMINDER_INTERVAL, DEFAULT_REMINDER_LIMIT, TeamMCPServer, find_free_port
from .wezterm import ensure_installed, list_workspace
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

    # ── new ──────────────────────────────────────────────────────────────────

    _SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

    def new(self, workstream_id: str, plan_path: str | None = None) -> None:
        """Create workstream dirs, copy plan, spawn orchestrator, start MCP server (blocks)."""
        ensure_installed()
        if not self._SAFE_ID_RE.match(workstream_id):
            raise ValueError(
                f"workstream_id must contain only alphanumerics, hyphens, and underscores; "
                f"got {workstream_id!r}"
            )
        ws_dir = self._ws_dir(workstream_id)
        if ws_dir.exists():
            logger.error(f"Workstream '{workstream_id}' already exists at {ws_dir}")
            return

        if plan_path and not Path(plan_path).exists():
            logger.error(f"Plan file not found: {plan_path}")
            return

        ws_dir.mkdir(parents=True)
        (ws_dir / "tickets").mkdir()
        (ws_dir / "worktrees").mkdir()

        # Copy or create plan.toml
        plan_dest = ws_dir / "plan.toml"
        if plan_path:
            src = Path(plan_path)
            shutil.copy2(src, plan_dest)
            logger.info(f"Copied plan from {src} to {plan_dest}")
        else:
            plan_dest.write_text(
                f'[workstream]\nid = "{workstream_id}"\ndescription = ""\n\n'
                f'[[goal]]\nid = "G1"\ndescription = ""\n\n'
                f'[[acceptance]]\ncriterion = ""\n',
                encoding="utf-8",
            )
            logger.info(f"Created empty plan at {plan_dest}")

        logger.info(f"Workstream '{workstream_id}' created at {ws_dir}")

        # Pick a free port and persist it so `agent run` can read it
        port = find_free_port()
        (ws_dir / "mcp.port").write_text(str(port), encoding="utf-8")

        # Spawn orchestrator — _agent_run always returns after spawn;
        # the MCP server.run() below is the blocking mechanism.
        _agent_run(
            self._ctx,
            workspace=workstream_id,
            role="orchestrator",
            workstream=workstream_id,
            mcp_port=port,
        )

        # Start MCP server — blocks until Ctrl+C, then kills all agent panes
        server = self._make_server(workstream_id, port)
        server.run()

    # ── attach ───────────────────────────────────────────────────────────────

    def attach(self, workstream_id: str) -> None:
        """Re-attach to an existing workstream by restarting the MCP server (blocks)."""
        ensure_installed()
        ws_dir = self._ws_dir(workstream_id)
        if not ws_dir.exists():
            logger.error(f"Workstream '{workstream_id}' does not exist")
            return

        port_file = ws_dir / "mcp.port"
        if port_file.exists():
            try:
                port = int(port_file.read_text(encoding="utf-8").strip())
            except ValueError:
                port = find_free_port()
                port_file.write_text(str(port), encoding="utf-8")
        else:
            port = find_free_port()
            port_file.write_text(str(port), encoding="utf-8")

        server = self._make_server(workstream_id, port)
        server.run()

    # ── status ───────────────────────────────────────────────────────────────

    def status(self, workstream_id: str | None = None) -> None:
        """Print workstream status: panes and ticket summaries."""
        ensure_installed()
        if workstream_id:
            self._print_status(workstream_id)
        else:
            agent_dir = self._root / "_agent"
            if not agent_dir.exists():
                logger.info("No workstreams found.")
                return
            for entry in sorted(agent_dir.iterdir()):
                if entry.is_dir() and not entry.name.startswith("."):
                    self._print_status(entry.name)

    def _print_status(self, workstream_id: str) -> None:
        ws_dir = self._ws_dir(workstream_id)

        print(f"\n{'='*60}")
        print(f"Workstream: {workstream_id}")
        print(f"{'='*60}")

        panes = list_workspace(workstream_id)
        if panes:
            print(f"\nPanes ({len(panes)}):")
            for p in panes:
                print(f"  pane_id={p.get('pane_id')}  title={p.get('title', '?')}")
        else:
            print("\nNo active panes.")

        tickets_dir = ws_dir / "tickets"
        if tickets_dir.exists():
            ticket_files = sorted(tickets_dir.glob("*.toml"))
            if ticket_files:
                print(f"\nTickets ({len(ticket_files)}):")
                for tf in ticket_files:
                    try:
                        data = tomllib.loads(tf.read_text(encoding="utf-8"))
                        ticket = data.get("ticket", {})
                        tid = ticket.get("id", tf.stem)
                        status = ticket.get("status", "unknown")
                        title = ticket.get("title", "")
                        print(f"  [{status:8s}] {tid}: {title}")
                    except Exception:
                        print(f"  [error   ] {tf.stem}: could not parse")
            else:
                print("\nNo tickets yet.")
        else:
            print("\nNo tickets directory.")

        print()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _make_server(self, workstream_id: str, port: int) -> TeamMCPServer:
        interval = int(
            self._ctx.tool_config.get("idle_reminder_interval", DEFAULT_REMINDER_INTERVAL)
        )
        limit = int(
            self._ctx.tool_config.get("idle_reminder_limit", DEFAULT_REMINDER_LIMIT)
        )
        return TeamMCPServer(workstream_id, port, interval, limit)
