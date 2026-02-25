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
from .ticket_mcp import _ROLE_ALLOWED_TRANSITIONS, _tool_mark_criteria, _tool_reset_ticket, _tool_update_ticket
from .worktree import ensure_worktree, remove_worktree

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


def _has_reviewable_changes(workspace_root: Path) -> bool:
    """Check if there are any changes for a reviewer to review.

    Returns True if there are uncommitted changes (staged or unstaged)
    or if the current branch has commits diverging from the default branch.
    """
    cwd = str(workspace_root)

    # Check uncommitted changes (staged + unstaged)
    diff = subprocess.run(
        ["git", "diff", "HEAD", "--quiet"],
        cwd=cwd, capture_output=True,
    )
    if diff.returncode != 0:
        return True

    # Check untracked files
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=cwd, capture_output=True, text=True,
    )
    if untracked.stdout.strip():
        return True

    # Check branch diff from common default branches
    for base in ("main", "master"):
        log = subprocess.run(
            ["git", "log", f"{base}..HEAD", "--oneline", "-1"],
            cwd=cwd, capture_output=True, text=True,
        )
        if log.returncode == 0 and log.stdout.strip():
            return True

    return False


_SAFE_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_ticket_id(value: str, field: str) -> None:
    """Raise ValueError if *value* is not safe for use as a path component."""
    if not value:
        raise ValueError(f"{field} must not be empty")
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"{field} contains path separators: {value!r}")
    if not _SAFE_AGENT_ID_RE.match(value):
        raise ValueError(f"{field} contains unsafe characters: {value!r}")



def _prepare_ticket_session(
    tool_ctx: ToolContext,
    role: str,
    ticket: str,
    agent_cwd: Path,
) -> tuple[str, str]:
    """Load ticket, validate lifecycle, build prompt.

    Returns ``(prompt, role_prompt)``.  Exits on validation failure.
    """
    _validate_ticket_id(ticket, "ticket")
    ticket_path = tool_ctx.workspace_root / "_agent" / "tickets" / f"{ticket}.json"

    if not ticket_path.exists():
        logger.error(f"Ticket file not found: {ticket_path}")
        sys.exit(1)

    ticket_content = ticket_path.read_text(encoding="utf-8")

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

    if role == "reviewer" and not _has_reviewable_changes(agent_cwd):
        logger.error(
            "No reviewable changes found (no uncommitted diff, no branch "
            "diff from default branch). Nothing for reviewer to review."
        )
        sys.exit(1)

    repo_cmd = tool_ctx.tokens.get("repo", "./repo")
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

    return prompt, role_prompt


def _process_agent_output(
    workspace_root: Path,
    ticket: str,
    role: str,
    proc: subprocess.CompletedProcess[str],
) -> str | None:
    """Parse structured JSON output from a headless agent and apply ticket updates.

    Claude Code ``--output-format json`` wraps output in an envelope::

        {"type": "result", "subtype": "success"|"error_max_turns",
         "is_error": bool, "structured_output": {...}, ...}
    """
    if proc.returncode != 0:
        logger.error(f"Agent exited with code {proc.returncode}")

    try:
        envelope = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        logger.error("Agent produced invalid JSON output — ticket not updated")
        print(proc.stdout)
        return proc.stdout

    if not isinstance(envelope, dict):
        logger.error("Agent output is not a JSON object — ticket not updated")
        print(proc.stdout)
        return proc.stdout

    if envelope.get("is_error"):
        subtype = envelope.get("subtype", "")
        if subtype == "error_max_turns" and role == "worker":
            logger.warning("Worker exhausted turn limit — setting ticket to in_progress")
            update_args = {
                "ticket_id": ticket,
                "status": "in_progress",
                "notes": "Agent exhausted turn limit",
            }
            update_result = _tool_update_ticket(workspace_root, update_args, role="worker")
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
        logger.error("Agent output missing ticket_id — ticket not updated")
        print(proc.stdout)
        return proc.stdout

    if output["ticket_id"] != ticket:
        logger.error(
            f"Agent returned ticket_id={output['ticket_id']!r} "
            f"but was assigned {ticket!r} — ticket not updated"
        )
        print(proc.stdout)
        return proc.stdout

    # Apply criteria from reviewer structured output
    if role == "reviewer" and "criteria" in output:
        criteria_bools = output["criteria"]
        met_indices = [i for i, v in enumerate(criteria_bools) if v]
        unmet_indices = [i for i, v in enumerate(criteria_bools) if not v]
        if met_indices:
            _tool_mark_criteria(workspace_root,
                {"ticket_id": ticket, "indices": met_indices, "met": True},
                role=role)
        if unmet_indices:
            _tool_mark_criteria(workspace_root,
                {"ticket_id": ticket, "indices": unmet_indices, "met": False},
                role=role)

    # Apply the update to the ticket JSON
    update_args: dict[str, str] = {"ticket_id": ticket}
    for field in ("status", "notes", "result", "feedback"):
        if field in output:
            update_args[field] = output[field]

    update_result = _tool_update_ticket(workspace_root, update_args, role=role)
    if update_result.get("isError"):
        logger.error(f"Ticket update failed: {update_result['text']}")
        error_output = {**output, "error": update_result["text"]}
        result = json.dumps(error_output, indent=2)
        print(result)
        return result

    logger.info(f"Ticket {ticket} updated: {update_result['text']}")
    result = json.dumps(output, indent=2)
    print(result)
    return result


def _agent_run(tool_ctx: ToolContext, args: dict[str, Any]) -> str | None:
    """Launch an agent session.

    Follows the ``RepoTool.execute(ctx, args)`` signature so that the
    full tool config (merged defaults < config.yaml < CLI) arrives in
    *args* — no individual parameter threading required.

    With ``role`` + ``ticket``: runs headless, returns result text.
    Without: replaces this process with interactive Claude.
    """
    role = args.get("role")
    ticket = args.get("ticket")
    agent_cwd = tool_ctx.workspace_root

    # Worktree setup — always for ticket-based runs
    if role and ticket:
        agent_cwd = ensure_worktree(tool_ctx.workspace_root, ticket)

    # Session setup — ticket or interactive
    if role and ticket:
        prompt, role_prompt = _prepare_ticket_session(
            tool_ctx, role, ticket, agent_cwd,
        )
    else:
        prompt = None
        role = "orchestrator"
        repo_cmd = tool_ctx.tokens.get("repo", "./repo")
        role_prompt = _render_role_prompt(
            role,
            repo_cmd=repo_cmd,
            framework_root=tool_ctx.tokens.get("framework_root", ""),
        )

    # Build command — tool_config flows as a dict
    rules_path = _find_rules_file(
        tool_ctx.workspace_root,
        configured=args.get("allowlist"),
    )
    cmd = _backend.build_command(
        prompt=prompt,
        role=role,
        role_prompt=role_prompt,
        rules_path=rules_path,
        project_root=tool_ctx.workspace_root,
        tool_config=args,
    )

    # Interactive mode — replace process
    if prompt is None:
        logger.info("Starting interactive agent session")
        if sys.platform == "win32":
            proc = subprocess.run(cmd, cwd=str(agent_cwd))
            sys.exit(proc.returncode)
        else:
            os.chdir(agent_cwd)
            os.execvp(cmd[0], cmd)

    # Headless mode
    logger.info(f"Running headless agent: role={role}, ticket={ticket}")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        text=True,
        cwd=str(agent_cwd),
        env=env,
    )

    return _process_agent_output(tool_ctx.workspace_root, ticket, role, proc)


# ── Click Command ────────────────────────────────────────────────────


def _reset_ticket(workspace_root: Path, ticket_id: str) -> None:
    """Reset a ticket to 'todo' — delegates to ticket_mcp._tool_reset_ticket."""
    result = _tool_reset_ticket(workspace_root, {"ticket_id": ticket_id})
    if result.get("isError"):
        raise click.ClickException(result["text"])
    logger.info(result["text"])


def _make_agent_command(tool: RepoTool) -> click.Group:
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
    @click.pass_context
    def agent(ctx: click.Context,
              role: str | None, ticket: str | None,
              debug_hooks: bool) -> None:
        """Launch an agent session."""
        if ctx.invoked_subcommand is not None:
            return
        if bool(role) != bool(ticket):
            raise click.UsageError("--role and --ticket must be used together")
        tool_ctx = _ctx_from_click(ctx)
        # Merge: tool_config < CLI flags (matching framework convention)
        args: dict[str, Any] = dict(tool_ctx.tool_config)
        if role is not None:
            args["role"] = role
        if ticket is not None:
            args["ticket"] = ticket
        if debug_hooks:
            args["debug_hooks"] = True
        tool.execute(tool_ctx, args)

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

    @agent.group(name="worktree", help="Manage agent worktrees.")
    def worktree_group() -> None:
        pass

    @worktree_group.command(name="remove")
    @click.argument("ticket_id")
    @click.pass_context
    def worktree_remove(ctx: click.Context, ticket_id: str) -> None:
        """Remove the worktree for a ticket."""
        tool_ctx = _ctx_from_click(ctx)
        _validate_ticket_id(ticket_id, "ticket_id")
        remove_worktree(tool_ctx.workspace_root, ticket_id)

    return agent


# ── AgentTool ────────────────────────────────────────────────────────


class AgentTool(RepoTool):
    name = "agent"
    help = "Run coding agents with workflows tailored for this repository."

    def create_click_command(self) -> click.BaseCommand | None:
        return _make_agent_command(self)

    def setup(self, cmd: click.Command) -> click.Command:
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {}

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        _agent_run(ctx, args)
