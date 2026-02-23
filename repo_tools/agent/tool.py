"""Agent tool — launches coding agents with repo-specific config.

``./repo agent`` starts an interactive Claude session.
``./repo agent --role worker --ticket G1_1`` runs headless.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from ..cli import _build_tool_context
from ..core import RepoTool, ToolContext, logger
from .claude import Claude
from .ticket_mcp import _ROLE_ALLOWED_TRANSITIONS, _tool_reset_ticket, _tool_update_ticket

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
    so that every role receives shared context.
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


def _validate_ticket_id(value: str, field: str) -> None:
    """Raise ValueError if *value* is not safe for use as a path component."""
    if not value:
        raise ValueError(f"{field} must not be empty")
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"{field} contains path separators: {value!r}")
    if not _SAFE_AGENT_ID_RE.match(value):
        raise ValueError(f"{field} contains unsafe characters: {value!r}")


def _agent_run(
    tool_ctx: ToolContext,
    role: str | None = None,
    ticket: str | None = None,
    debug_hooks: bool = False,
    worktree: bool = False,
) -> str | None:
    """Launch an agent session.

    With ``--ticket``: runs headless ``claude -p``, blocks until done,
    returns the result text.

    Without ``--ticket``: replaces this process with interactive Claude.
    """
    debug_hooks = debug_hooks or tool_ctx.tool_config.get("debug_hooks", False)
    max_turns = tool_ctx.tool_config.get("max_turns")
    repo_cmd = tool_ctx.tokens.get("repo", "./repo")

    # Build role prompt for headless mode
    role_prompt = None
    prompt = None
    if role and ticket:
        _validate_ticket_id(ticket, "ticket")
        ticket_path = tool_ctx.workspace_root / "_agent" / "tickets" / f"{ticket}.json"

        if not ticket_path.exists():
            logger.error(f"Ticket file not found: {ticket_path}")
            sys.exit(1)

        ticket_content = ticket_path.read_text(encoding="utf-8")

        # Lifecycle gating — enforce role/status constraints
        try:
            ticket_data = json.loads(ticket_content)
            ticket_status = ticket_data.get("ticket", {}).get("status", "unknown")
        except (json.JSONDecodeError, AttributeError):
            logger.error(f"Ticket file is not valid JSON: {ticket_path}")
            sys.exit(1)

        valid_statuses = {s for s, _ in _ROLE_ALLOWED_TRANSITIONS.get(role, set())}
        if valid_statuses and ticket_status not in valid_statuses:
            logger.error(
                f"{role} requires ticket status in {sorted(valid_statuses)}, "
                f"got '{ticket_status}'"
            )
            sys.exit(1)

        role_prompt = _render_role_prompt(
            role,
            ticket_id=ticket,
            ticket_path=str(ticket_path),
            project_root=str(tool_ctx.workspace_root),
            repo_cmd=repo_cmd,
            framework_root=tool_ctx.tokens.get("framework_root", ""),
        )

        prompt = (
            f"You are working on ticket {ticket}.\n\n"
            f"Ticket content:\n```json\n{ticket_content}\n```\n\n"
            f"Read the ticket and begin your work."
        )

    if role_prompt is None:
        # Interactive session — set orchestrator role explicitly so MCP
        # permissions and allowlist rules apply correctly.
        role = "orchestrator"
        role_prompt = _render_role_prompt(
            role,
            repo_cmd=repo_cmd,
            framework_root=tool_ctx.tokens.get("framework_root", ""),
        )

    rules_path = _find_rules_file(
        tool_ctx.workspace_root,
        configured=tool_ctx.tool_config.get("allowlist"),
    )
    # Derive worktree name from ticket when available
    worktree_name = None
    if worktree:
        if ticket:
            worktree_name = ticket
        else:
            worktree_name = ""  # let Claude Code auto-generate

    ruff_select = tool_ctx.tool_config.get("ruff_select")
    ruff_ignore = tool_ctx.tool_config.get("ruff_ignore")

    cmd = _backend.build_command(
        prompt=prompt,
        role=role,
        role_prompt=role_prompt,
        rules_path=rules_path,
        project_root=tool_ctx.workspace_root,
        debug_hooks=debug_hooks,
        worktree=worktree_name,
        max_turns=max_turns,
        ruff_select=ruff_select,
        ruff_ignore=ruff_ignore,
    )

    if prompt is not None:
        # Headless mode — run subprocess, capture output (stderr passes through)
        logger.info(f"Running headless agent: role={role}, ticket={ticket}")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            text=True,
            cwd=str(tool_ctx.workspace_root),
            env=env,
        )

        if proc.returncode != 0:
            logger.error(f"Agent exited with code {proc.returncode}")

        # Parse structured JSON output and apply ticket update.
        # Claude Code --output-format json always wraps output in an
        # envelope: {"type":"result", "subtype":"success"|"error_max_turns",
        #            "is_error":bool, "structured_output":{...}, ...}.
        try:
            envelope = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            logger.error(f"Agent produced invalid JSON output — ticket not updated")
            print(proc.stdout)
            return proc.stdout

        if not isinstance(envelope, dict):
            logger.error(f"Agent output is not a JSON object — ticket not updated")
            print(proc.stdout)
            return proc.stdout

        if envelope.get("is_error"):
            subtype = envelope.get("subtype", "")
            if subtype == "error_max_turns" and role == "worker":
                logger.warning(f"Worker exhausted turn limit — setting ticket to in_progress")
                update_args = {
                    "ticket_id": ticket,
                    "status": "in_progress",
                    "notes": "Agent exhausted turn limit",
                }
                update_result = _tool_update_ticket(tool_ctx.workspace_root, update_args, role="worker")
                if update_result.get("isError"):
                    logger.error(f"Ticket update failed: {update_result['text']}")
                else:
                    logger.info(f"Ticket {ticket} updated: {update_result['text']}")
            else:
                logger.error(f"Agent reported error (subtype={subtype!r}) — ticket not updated")
            print(proc.stdout)
            return proc.stdout

        output = envelope.get("structured_output")

        if not isinstance(output, dict) or "ticket_id" not in output:
            logger.error(f"Agent output missing ticket_id — ticket not updated")
            print(proc.stdout)
            return proc.stdout

        if output["ticket_id"] != ticket:
            logger.error(
                f"Agent returned ticket_id={output['ticket_id']!r} "
                f"but was assigned {ticket!r} — ticket not updated"
            )
            print(proc.stdout)
            return proc.stdout

        # Apply the update to the ticket JSON
        update_args = {"ticket_id": ticket}
        for field in ("status", "notes", "result", "feedback"):
            if field in output:
                update_args[field] = output[field]

        update_result = _tool_update_ticket(tool_ctx.workspace_root, update_args, role=role)
        if update_result.get("isError"):
            logger.error(f"Ticket update failed: {update_result['text']}")
        else:
            logger.info(f"Ticket {ticket} updated: {update_result['text']}")

        result = json.dumps(output, indent=2)
        print(result)
        return result

    # Interactive mode — replace process (Unix) or delegate (Windows)
    logger.info("Starting interactive agent session")
    if sys.platform == "win32":
        proc = subprocess.run(cmd, cwd=str(tool_ctx.workspace_root))
        sys.exit(proc.returncode)
    else:
        os.execvp(cmd[0], cmd)


# ── Click Command ────────────────────────────────────────────────────


def _reset_ticket(workspace_root: Path, ticket_id: str) -> None:
    """Reset a ticket to 'todo' — delegates to ticket_mcp._tool_reset_ticket."""
    result = _tool_reset_ticket(workspace_root, {"ticket_id": ticket_id})
    if result.get("isError"):
        raise click.ClickException(result["text"])
    logger.info(result["text"])


def _make_agent_command() -> click.Group:
    """Build the ``agent`` Click group."""

    @click.group(
        name="agent",
        help="Run coding agents with workflows tailored for this repository.",
        invoke_without_command=True,
    )
    @click.option("--role", default=None, type=click.Choice(["worker", "reviewer"]),
                  help="Role for this agent")
    @click.option("--ticket", default=None, help="Ticket ID (for worker/reviewer roles)")
    @click.option("--debug-hooks", is_flag=True, default=False,
                  help="Log hook decisions to _agent/hooks.log")
    @click.option("--worktree", "-w", is_flag=True, default=False,
                  help="Run in an isolated git worktree")
    @click.pass_context
    def agent(ctx: click.Context,
              role: str | None, ticket: str | None,
              debug_hooks: bool, worktree: bool) -> None:
        """Launch an agent session."""
        if ctx.invoked_subcommand is not None:
            return
        if bool(role) != bool(ticket):
            raise click.UsageError("--role and --ticket must be used together")
        tool_ctx = _ctx_from_click(ctx)
        _agent_run(
            tool_ctx,
            role=role,
            ticket=ticket,
            debug_hooks=debug_hooks,
            worktree=worktree,
        )

    @agent.group(name="ticket", help="Manage agent tickets.")
    def ticket_group() -> None:
        pass

    @ticket_group.command(name="reset")
    @click.argument("ticket_id")
    @click.pass_context
    def ticket_reset(ctx: click.Context, ticket_id: str) -> None:
        """Reset a ticket to 'todo' status."""
        tool_ctx = _ctx_from_click(ctx)
        _reset_ticket(tool_ctx.workspace_root, ticket_id)

    return agent


# ── AgentTool ────────────────────────────────────────────────────────


class AgentTool(RepoTool):
    name = "agent"
    help = "Run coding agents with workflows tailored for this repository."

    def create_click_command(self) -> click.BaseCommand | None:
        return _make_agent_command()

    def setup(self, cmd: click.Command) -> click.Command:
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {}

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        pass  # Handled by create_click_command()
