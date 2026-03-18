"""Agent tool — launches coding agents with repo-specific config.

``./repo agent`` starts an interactive Claude session.
``./repo agent --role worker --ticket G1_1`` runs headless.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from ..cli import _build_tool_context
from ..core import RepoTool, ToolContext, logger
from .claude import ClaudeBackend
from .tickets import _ROLE_ALLOWED_TRANSITIONS, _tool_mark_criteria, _tool_reset_ticket, _tool_update_ticket
from .worktree import ensure_worktree, remove_worktree

_backend: ClaudeBackend | None = None


def _setup_file_logging(
    workspace_root: Path,
    role: str,
    ticket: str | None,
) -> logging.FileHandler | None:
    """Attach a file handler to the repo_tools logger.

    Logs go to ``_agent/logs/<role>-<ticket>-<timestamp>.log`` for headless
    sessions or ``_agent/logs/interactive-<timestamp>.log`` for interactive.
    """
    log_dir = workspace_root / "_agent" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{role}-{ticket}-{ts}.log" if ticket else f"interactive-{ts}.log"
    handler = logging.FileHandler(log_dir / filename, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.getLogger("repo_tools").addHandler(handler)
    return handler


def _ensure_backend(args: dict[str, Any]) -> ClaudeBackend:
    """Lazy-init the backend, respecting ``args["backend"]``."""
    global _backend
    if _backend is None:
        from .claude import get_backend
        _backend = get_backend(args.get("backend"))
    return _backend


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


def _find_extra_rules(workspace_root: Path, config: dict) -> list[Path]:
    """Find project-level allowlist extensions from ``agent.allowlist_extra``."""
    extra_file = config.get("agent", {}).get("allowlist_extra")
    if not extra_file:
        return []
    candidate = workspace_root / extra_file
    if candidate.exists():
        return [candidate]
    logger.warning("Extra allowlist file not found: %s", candidate)
    return []


def _render_role_prompt(role: str, **kwargs: str) -> str:
    """Load prompt template for a role and format placeholders.

    If ``prompts/common.txt`` exists it is prepended to the role template
    so that every role receives shared context.
    """
    prompts_dir = Path(__file__).parent / "prompts"
    template_file = prompts_dir / f"{role}.txt"
    if not template_file.exists():
        if role in ("worker", "reviewer", "orchestrator"):
            raise FileNotFoundError(
                f"Missing prompt template for known role {role!r}: {template_file}"
            )
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
        cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL,
    )
    if diff.returncode != 0:
        return True

    # Check untracked files
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    if untracked.stdout.strip():
        return True

    # Check branch diff from common default branches
    for base in ("main", "master"):
        log = subprocess.run(
            ["git", "log", f"{base}..HEAD", "--oneline", "-1"],
            cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        if log.returncode == 0 and log.stdout.strip():
            return True

    return False


_SAFE_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

def _augment_role_prompt(role_prompt: str, config: dict, role: str) -> str:
    """Append project-specific prompts to *role_prompt*."""
    prompts = config.get("agent", {}).get("prompts", {})

    common = prompts.get("common")
    if common:
        role_prompt += f"\n\n## Project Instructions\n\n{common}"

    role_specific = prompts.get(role)
    if role_specific:
        role_prompt += f"\n\n## Project Instructions ({role})\n\n{role_specific}"

    return role_prompt


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

    role_prompt = _render_role_prompt(
        role,
        ticket_id=ticket,
        ticket_path=str(ticket_path),
        project_root=str(tool_ctx.workspace_root),
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
    stdout: str,
    returncode: int,
) -> str | None:
    """Parse structured JSON output from a headless agent and apply ticket updates.

    The SDK reconstructs the same envelope format::

        {"type": "result", "subtype": "success"|"error_max_turns",
         "is_error": bool, "structured_output": {...}, ...}
    """
    if returncode != 0:
        logger.error(f"Agent exited with code {returncode}")

    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        logger.error("Agent produced invalid JSON output — ticket not updated")
        print(stdout)
        return stdout

    if not isinstance(envelope, dict):
        logger.error("Agent output is not a JSON object — ticket not updated")
        print(stdout)
        return stdout

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
        print(stdout)
        return stdout

    output = envelope.get("structured_output")

    if not isinstance(output, dict) or "ticket_id" not in output:
        logger.error("Agent output missing ticket_id — ticket not updated")
        print(stdout)
        return stdout

    if output["ticket_id"] != ticket:
        logger.error(
            f"Agent returned ticket_id={output['ticket_id']!r} "
            f"but was assigned {ticket!r} — ticket not updated"
        )
        print(stdout)
        return stdout

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

    _setup_file_logging(tool_ctx.workspace_root, role or "orchestrator", ticket)

    # Worktree setup — always for ticket-based runs
    if role and ticket:
        agent_cwd = ensure_worktree(
            tool_ctx.workspace_root, ticket, base_ref=args.get("branch"),
        )

    # Session setup — ticket or interactive
    if role and ticket:
        prompt, role_prompt = _prepare_ticket_session(
            tool_ctx, role, ticket, agent_cwd,
        )
        role_prompt = _augment_role_prompt(role_prompt, tool_ctx.config, role)
    else:
        prompt = None
        role = "orchestrator"
        role_prompt = _render_role_prompt(role)
        role_prompt = _augment_role_prompt(role_prompt, tool_ctx.config, "orchestrator")

    # Resolve rules file
    rules_path = _find_rules_file(
        tool_ctx.workspace_root,
        configured=args.get("allowlist"),
    )
    extra_rules_paths = _find_extra_rules(tool_ctx.workspace_root, tool_ctx.config)

    # Headless roles (worker/reviewer) always use CLI backend — the SDK
    # backend launches an in-process Claude Code session which cannot run
    # nested inside another Claude Code process.
    if role and ticket:
        args = {**args, "backend": "cli"}
    backend = _ensure_backend(args)

    if prompt is None:
        # Interactive mode — with event loop support
        from .events import (
            clear_subscriptions,
            has_subscriptions,
            poll_until_fired,
            pop_subscription,
            resolve_event_config,
            run_payload,
        )

        interactive_kwargs = dict(
            role_prompt=role_prompt,
            rules_path=rules_path,
            extra_rules_paths=extra_rules_paths,
            project_root=tool_ctx.workspace_root,
            tool_config=args,
            project_config=tool_ctx.config,
            cwd=agent_cwd,
        )

        logger.info("Starting interactive agent session")
        rc, session_id = backend.run_interactive(**interactive_kwargs)

        # Event loop: run → poll → resume same session with payload
        while has_subscriptions():
            sub = pop_subscription()
            if sub is None:
                break
            group, event = sub["group"], sub["event"]
            logger.info(f"Event subscription: {group}.{event} — polling...")
            try:
                event_cfg = resolve_event_config(args, group, event)
            except KeyError as exc:
                logger.error(f"Bad event subscription: {exc}")
                continue
            merged_tokens = {**tool_ctx.tokens, **sub.get("tokens", {})}
            poll_until_fired(event_cfg, merged_tokens, tool_ctx.config, agent_cwd)
            event_payload = run_payload(event_cfg, merged_tokens, tool_ctx.config, agent_cwd)
            resume_prompt = f"Event '{group}.{event}' fired.\n\n{event_payload}"
            logger.info(f"Event {group}.{event} fired, resuming session {session_id}")
            rc, session_id = backend.run_interactive(
                **interactive_kwargs,
                initial_prompt=resume_prompt,
                resume=session_id,
            )

        clear_subscriptions()
        sys.exit(rc)

    # Headless mode
    logger.info(f"Running headless agent: role={role}, ticket={ticket}")
    stdout, returncode = backend.run_headless(
        prompt=prompt,
        role=role,
        role_prompt=role_prompt,
        rules_path=rules_path,
        extra_rules_paths=extra_rules_paths,
        project_root=tool_ctx.workspace_root,
        tool_config=args,
        project_config=tool_ctx.config,
        cwd=agent_cwd,
    )

    return _process_agent_output(tool_ctx.workspace_root, ticket, role, stdout, returncode)


# ── Click Command ────────────────────────────────────────────────────


def _reset_ticket(workspace_root: Path, ticket_id: str) -> None:
    """Reset a ticket to 'todo' — delegates to tickets._tool_reset_ticket."""
    result = _tool_reset_ticket(workspace_root, {"ticket_id": ticket_id})
    if result.get("isError"):
        raise click.ClickException(result["text"])
    logger.info(result["text"])


# ── AgentTool ────────────────────────────────────────────────────────


class AgentTool(RepoTool):
    name = "agent"
    help = "Run coding agents with workflows tailored for this repository."

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option(
            "--role", default=None,
            type=click.Choice(["worker", "reviewer"]),
            help="Role for this agent",
        )(cmd)
        cmd = click.option(
            "--ticket", default=None,
            help="Ticket ID (for worker/reviewer roles)",
        )(cmd)
        cmd = click.option(
            "--debug-hooks", is_flag=True, default=None,
            help="Log hook decisions to _agent/hooks.log",
        )(cmd)
        cmd = click.option(
            "--backend", default=None,
            type=click.Choice(["cli", "sdk"]),
            help="Backend to use (cli or sdk)",
        )(cmd)
        cmd = click.option(
            "--max-turns", default=None, type=int,
            help="Maximum turns for headless mode",
        )(cmd)
        cmd = click.option(
            "--branch", default=None,
            help="Base branch/ref for worktree (default: HEAD)",
        )(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {
            "role": None,
            "ticket": None,
            "debug_hooks": False,
            "backend": "cli",
            "max_turns": None,
            "branch": None,
        }

    def register_subcommands(self, group: click.Group) -> None:
        @group.group(name="ticket", help="Manage agent tickets.")
        def ticket_group() -> None:
            pass

        @ticket_group.command(name="reset")
        @click.argument("ticket_id")
        @click.pass_context
        def ticket_reset(ctx: click.Context, ticket_id: str) -> None:
            """Reset a ticket to 'todo' status."""
            tool_ctx = _ctx_from_click(ctx)
            _reset_ticket(tool_ctx.workspace_root, ticket_id)

        @group.group(name="worktree", help="Manage agent worktrees.")
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

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        role = args.get("role")
        ticket = args.get("ticket")
        if bool(role) != bool(ticket):
            raise click.UsageError("--role and --ticket must be used together")
        _agent_run(ctx, args)
