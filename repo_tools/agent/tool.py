"""Agent tool — launches coding agents with repo-specific config.

Provides the ``agent`` Click group with subcommands:
  run   — launch a single agent (solo or team role)
  team  — start a multi-agent workstream (creates if new, resumes if existing)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import click

from ..cli import _build_tool_context
from ..core import RepoTool, ToolContext, logger
from .claude import Claude
from .wezterm import PaneSession, activate_pane, ensure_installed, list_workspace, spawn_in_workspace

_backend = Claude()


# ── Helpers ──────────────────────────────────────────────────────────


def _ctx_from_click(ctx: click.Context) -> ToolContext:
    """Build ToolContext from click context obj (inherited from parent group)."""
    return _build_tool_context(ctx.obj, "agent")


def _find_rules_file(workspace_root: Path, configured: str | None = None) -> Path:
    """Find rules file: configured path first, then framework default."""
    if configured:
        candidate = workspace_root / configured
        if candidate.exists():
            return candidate
        logger.warning(f"Configured rules file not found: {configured}")
    return Path(__file__).parent / "allowlist_default.toml"


def _render_role_prompt(role: str, **kwargs: str) -> str:
    """Load prompt template for a role and format placeholders.

    If ``prompts/common.txt`` exists it is prepended to the role template
    so that every role receives shared context (framework overview, filesystem
    boundaries, etc.).
    """
    prompts_dir = Path(__file__).parent / "prompts"
    template_file = prompts_dir / f"{role}.txt"
    if not template_file.exists():
        return ""
    parts: list[str] = []
    common_file = prompts_dir / "common.txt"
    if common_file.exists():
        parts.append(common_file.read_text(encoding="utf-8"))
    parts.append(template_file.read_text(encoding="utf-8"))
    return "\n".join(parts).format_map(kwargs)


_SAFE_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_agent_id(value: str, field: str) -> None:
    """Raise ValueError if *value* is not safe for use as a path component or branch name."""
    if not value:
        raise ValueError(f"{field} must not be empty")
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"{field} contains path separators: {value!r}")
    if not _SAFE_AGENT_ID_RE.match(value):
        raise ValueError(f"{field} contains unsafe characters: {value!r}")


def _setup_worktree(workspace_root: Path, workstream: str, ticket: str) -> Path:
    """Create a git worktree for the ticket and return its path."""
    _validate_agent_id(workstream, "workstream")
    _validate_agent_id(ticket, "ticket")
    agent_dir = workspace_root / "_agent" / workstream
    worktree_path = agent_dir / "worktrees" / ticket
    branch = f"agent/{workstream}/{ticket}"

    if worktree_path.exists():
        return worktree_path

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(worktree_path)],
            cwd=str(workspace_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        # Branch may already exist
        subprocess.run(
            ["git", "worktree", "add", str(worktree_path), branch],
            cwd=str(workspace_root),
            check=True,
            capture_output=True,
            text=True,
        )
    return worktree_path


def _register_pane(
    port: int, pane_id: int, role: str, workstream: str, ticket: str
) -> None:
    """Register a spawned pane with the MCP server for idle tracking."""
    data = json.dumps(
        {"pane_id": pane_id, "role": role, "workstream": workstream, "ticket": ticket}
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/register",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2)
    except Exception as exc:
        logger.debug(f"Pane registration failed (port {port}): {exc}")


def _agent_run(
    tool_ctx: ToolContext,
    workspace: str | None = None,
    role: str | None = None,
    workstream: str | None = None,
    ticket: str | None = None,
    debug_hooks: bool = False,
    mcp_port: int | None = None,
) -> PaneSession | None:
    """Launch an agent — solo mode (original) or team mode (with role/workspace).

    Returns the PaneSession so callers can activate/manage the pane.
    """
    ensure_installed()
    cwd = str(tool_ctx.workspace_root)
    repo_cmd = tool_ctx.tokens.get("repo", "./repo")
    debug_hooks = debug_hooks or tool_ctx.tool_config.get("debug_hooks", False)

    logger.info(f"Starting agent... role={role}, workstream={workstream}, ticket={ticket}, debug_hooks={debug_hooks}")

    # Resolve MCP port from port file if not explicitly given
    if workstream and mcp_port is None:
        port_file = tool_ctx.workspace_root / "_agent" / workstream / "mcp.port"
        if port_file.exists():
            try:
                mcp_port = int(port_file.read_text(encoding="utf-8").strip())
            except ValueError:
                pass

    # Build role prompt if in team mode
    role_prompt = None
    worktree_path = None
    if role and workstream:
        agent_dir = tool_ctx.workspace_root / "_agent" / workstream
        ticket_path = ""
        branch = f"agent/{workstream}"

        if ticket:
            ticket_path = str(agent_dir / "tickets" / f"{ticket}.toml")
            branch = f"agent/{workstream}/{ticket}"
            worktree_path = _setup_worktree(tool_ctx.workspace_root, workstream, ticket)
            cwd = str(worktree_path)

        role_prompt = _render_role_prompt(
            role,
            workstream_id=workstream,
            ticket_id=ticket or "",
            worktree_path=str(worktree_path or cwd),
            ticket_path=ticket_path,
            branch=branch,
            project_root=str(tool_ctx.workspace_root),
            repo_cmd=repo_cmd,
            framework_root=tool_ctx.tokens.get("framework_root", ""),
        )

    rules_path = _find_rules_file(
        tool_ctx.workspace_root,
        configured=tool_ctx.tool_config.get("allowlist"),
    )
    cmd = _backend.build_command(
        role=role,
        role_prompt=role_prompt,
        rules_path=rules_path,
        project_root=tool_ctx.workspace_root,
        debug_hooks=debug_hooks,
        mcp_port=mcp_port,
        cwd=Path(cwd),
    )

    # Spawn in workspace or new window
    if workspace:
        session = spawn_in_workspace(cmd, workspace, cwd=cwd)
    else:
        session = PaneSession.spawn(cmd, cwd=cwd)

    if session is None:
        logger.error("Failed to obtain WezTerm pane.")
        sys.exit(1)

    logger.info(f"Agent running in WezTerm pane {session.pane_id}")
    activate_pane(session.pane_id)

    # Register worker/reviewer panes with the MCP server for idle tracking
    if mcp_port and role in ("worker", "reviewer") and workstream and ticket:
        _register_pane(mcp_port, session.pane_id, role, workstream, ticket)

    return session


# ── Click Group ──────────────────────────────────────────────────────


def _make_agent_group() -> click.Group:
    """Build the ``agent`` Click group with run/team subcommands."""

    @click.group(name="agent", help="Run coding agents with workflows tailored for this repository.")
    @click.pass_context
    def agent(ctx: click.Context) -> None:
        ctx.ensure_object(dict)

    # ── run ──

    @agent.command()
    @click.option("--workspace", default=None, help="WezTerm workspace name")
    @click.option("--role", default=None, type=click.Choice(["orchestrator", "worker", "reviewer"]),
                  help="Team role for this agent")
    @click.option("--workstream", default=None, help="Workstream ID")
    @click.option("--ticket", default=None, help="Ticket ID (for worker/reviewer roles)")
    @click.option("--debug-hooks", is_flag=True, default=False,
                  help="Log hook decisions to _agent/hooks.log")
    @click.pass_context
    def run(ctx: click.Context, workspace: str | None,
            role: str | None, workstream: str | None, ticket: str | None,
            debug_hooks: bool) -> None:
        """Launch a single agent session."""
        tool_ctx = _ctx_from_click(ctx)
        _agent_run(
            tool_ctx,
            workspace=workspace,
            role=role,
            workstream=workstream,
            ticket=ticket,
            debug_hooks=debug_hooks,
        )

    # ── team ──

    @agent.command()
    @click.argument("workstream_id")
    @click.pass_context
    def team(ctx: click.Context, workstream_id: str) -> None:
        """Start a multi-agent workstream (creates if new, resumes if existing)."""
        from .team import TeamManager
        tool_ctx = _ctx_from_click(ctx)
        mgr = TeamManager(tool_ctx)
        mgr.start(workstream_id)

    return agent


# ── AgentTool ────────────────────────────────────────────────────────


class AgentTool(RepoTool):
    name = "agent"
    help = "Run coding agents with workflows tailored for this repository."

    def create_click_command(self) -> click.BaseCommand | None:
        return _make_agent_group()

    def setup(self, cmd: click.Command) -> click.Command:
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {}

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        pass  # Handled by create_click_command()
