"""Claude Code backend — SDK-based.

Uses the claude-agent-sdk Python API to launch Claude Code sessions
with in-process hooks and MCP tools.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ...core import logger
from ..rules import check_command, load_rules

# Tools that are always pre-approved — all are read-only or local edits.
# Bash is excluded here; it is added per-role and gated by the PreToolUse hook.
_ALLOWED_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch"]

# JSON schemas for structured headless output, keyed by role.
_OUTPUT_SCHEMAS: dict[str, dict] = {
    "worker": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string"},
            "status": {"type": "string", "enum": ["verify", "in_progress"]},
            "notes": {"type": "string"},
        },
        "required": ["ticket_id", "status", "notes"],
        "additionalProperties": False,
    },
    "reviewer": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string"},
            "status": {"type": "string", "enum": ["closed", "todo"]},
            "result": {"type": "string", "enum": ["pass", "fail"]},
            "feedback": {"type": "string"},
            "criteria": {"type": "array", "items": {"type": "boolean"}},
        },
        "required": ["ticket_id", "status", "result", "feedback", "criteria"],
        "additionalProperties": False,
    },
}


# ── In-process hooks (no SDK dependency) ─────────────────────────────────────


def _make_check_bash_hook(
    rules_path: Path, project_root: Path, role: str | None,
):
    """Create a PreToolUse hook that checks Bash commands against the rules file."""

    async def check_bash(
        input_data: dict[str, Any], tool_use_id: str | None, context: dict,
    ) -> dict[str, Any]:
        command = input_data.get("tool_input", {}).get("command", "")
        cwd = Path(input_data.get("cwd", "."))

        rules = load_rules(rules_path, role=role)
        allowed, reason = check_command(
            command, rules, project_root=project_root, cwd=cwd,
        )

        if allowed:
            return {}

        try:
            rel_rules = rules_path.resolve().relative_to(
                project_root.resolve(),
            ).as_posix()
        except ValueError:
            rel_rules = rules_path.as_posix()

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Blocked: {reason}. Rules: {rel_rules}"
                ),
            }
        }

    return check_bash


def _make_approve_mcp_hook():
    """Create a PermissionRequest hook that auto-approves MCP tool calls."""

    async def approve_mcp(
        input_data: dict[str, Any], tool_use_id: str | None, context: dict,
    ) -> dict[str, Any]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }

    return approve_mcp


# ── In-process MCP tools ─────────────────────────────────────────────────────


def _make_lint_tool(
    default_select: str | None = None,
    default_ignore: str | None = None,
):
    """Create a lint MCP tool backed by call_lint()."""
    from claude_agent_sdk import tool

    from ..lint import call_lint as _call_lint

    @tool(
        "lint",
        "Run static analysis on a file or directory. "
        "Automatically detects language and runs the appropriate linter "
        "(ruff for Python, clang-tidy for C/C++). Returns plain-text diagnostics.",
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File or directory to lint. Defaults to '.'.",
                    "default": ".",
                },
            },
            "required": [],
        },
    )
    async def lint_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = _call_lint(
            args, default_select=default_select, default_ignore=default_ignore,
        )
        return {
            "content": [{"type": "text", "text": result.get("text", "")}],
            **({"isError": True} if result.get("isError") else {}),
        }

    return lint_tool


def _make_coderabbit_tool():
    """Create a coderabbit_review MCP tool backed by call_review()."""
    from claude_agent_sdk import tool

    from ..coderabbit import call_review as _call_review

    @tool(
        "coderabbit_review",
        "Run the CodeRabbit CLI to review code changes in a git worktree. "
        "Returns plain-text reviewer feedback. "
        "If the CLI is not installed or not authenticated, returns an error "
        "message instructing you to fall back to manual review.",
        {
            "type": "object",
            "properties": {
                "worktree_path": {
                    "type": "string",
                    "description": (
                        "Path to the git worktree whose changes should be "
                        "reviewed. Defaults to '.' (the current working "
                        "directory)."
                    ),
                    "default": ".",
                },
                "type": {
                    "type": "string",
                    "enum": ["committed", "uncommitted", "all"],
                    "default": "committed",
                    "description": (
                        "Which changes to review: 'committed' (default), "
                        "'uncommitted', or 'all'."
                    ),
                },
            },
            "required": [],
        },
    )
    async def coderabbit_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = _call_review(args)
        return {
            "content": [{"type": "text", "text": result.get("text", "")}],
            **({"isError": True} if result.get("isError") else {}),
        }

    return coderabbit_tool


def _make_ticket_tools(workspace_root: Path, role: str | None):
    """Create ticket MCP tools filtered by role."""
    from claude_agent_sdk import tool

    from ..ticket_mcp import (
        _ROLE_ALLOWED_TOOLS,
        _tool_create_ticket,
        _tool_delete_ticket,
        _tool_get_ticket,
        _tool_list_tickets,
        _tool_mark_criteria,
        _tool_reset_ticket,
        _tool_update_ticket,
    )

    allowed = _ROLE_ALLOWED_TOOLS.get(role or "orchestrator", set())
    tools = []

    def _mk_handler(fn):
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            result = fn(workspace_root, args, role=role)
            return {
                "content": [{"type": "text", "text": result.get("text", "")}],
                **({"isError": True} if result.get("isError") else {}),
            }
        return handler

    _TOOL_DEFS: list[tuple[str, str, dict]] = [
        (
            "list_tickets",
            "List all tickets with their id and status.",
            {"type": "object", "properties": {}, "required": []},
        ),
        (
            "get_ticket",
            "Return the full JSON content of a ticket.",
            {
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "Ticket identifier.",
                    },
                },
                "required": ["ticket_id"],
            },
        ),
        (
            "create_ticket",
            "Create a new ticket JSON file.",
            {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Short descriptive kebab-case id.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short task title.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed implementation instructions.",
                    },
                    "criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Acceptance criteria (optional).",
                    },
                },
                "required": ["id", "title", "description"],
            },
        ),
        (
            "update_ticket",
            "Update fields on an existing ticket. Only provided fields are changed.",
            {
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "Ticket identifier.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["todo", "in_progress", "verify", "closed"],
                        "description": "New ticket status.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Progress notes to set.",
                    },
                    "result": {
                        "type": "string",
                        "enum": ["pass", "fail", ""],
                        "description": "Review result.",
                    },
                    "feedback": {
                        "type": "string",
                        "description": "Review feedback.",
                    },
                    "description": {
                        "type": "string",
                        "description": "New ticket description.",
                    },
                },
                "required": ["ticket_id"],
            },
        ),
        (
            "reset_ticket",
            "Reset a ticket to 'todo' status, clearing progress and review.",
            {
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "Ticket identifier.",
                    },
                },
                "required": ["ticket_id"],
            },
        ),
        (
            "mark_criteria",
            "Mark specific acceptance criteria as met or unmet.",
            {
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "Ticket identifier.",
                    },
                    "indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Zero-based indices of criteria to update.",
                    },
                    "met": {
                        "type": "boolean",
                        "description": "Whether to mark as met (default true).",
                    },
                },
                "required": ["ticket_id", "indices"],
            },
        ),
        (
            "delete_ticket",
            "Delete a ticket JSON file.",
            {
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "Ticket identifier.",
                    },
                },
                "required": ["ticket_id"],
            },
        ),
    ]

    _TOOL_HANDLERS = {
        "list_tickets": _tool_list_tickets,
        "get_ticket": _tool_get_ticket,
        "create_ticket": _tool_create_ticket,
        "update_ticket": _tool_update_ticket,
        "reset_ticket": _tool_reset_ticket,
        "mark_criteria": _tool_mark_criteria,
        "delete_ticket": _tool_delete_ticket,
    }

    for name, desc, schema in _TOOL_DEFS:
        if name in allowed:
            handler_fn = _TOOL_HANDLERS[name]
            tools.append(tool(name, desc, schema)(_mk_handler(handler_fn)))

    return tools


# ── Async implementations ────────────────────────────────────────────────────


async def _run_headless(prompt: str, options: Any) -> tuple[str, int]:
    """Run a headless query and return (stdout_json, returncode)."""
    from claude_agent_sdk import ProcessError, ResultMessage, query

    result_msg = None
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, ResultMessage):
                result_msg = msg
    except ProcessError as exc:
        envelope = {"type": "result", "subtype": "error", "is_error": True}
        return (json.dumps(envelope), exc.exit_code or 1)

    if result_msg is None:
        envelope = {"type": "result", "subtype": "error", "is_error": True}
        return (json.dumps(envelope), 1)

    envelope = {
        "type": "result",
        "subtype": result_msg.subtype,
        "is_error": result_msg.is_error,
        "structured_output": result_msg.structured_output,
    }
    return (json.dumps(envelope), 1 if result_msg.is_error else 0)


async def _run_interactive(options: Any) -> int:
    """Run an interactive REPL session."""
    from claude_agent_sdk import ClaudeSDKClient

    from .render import render_message

    async with ClaudeSDKClient(options=options) as client:
        while True:
            try:
                user_input = input("\n> ")
            except (EOFError, KeyboardInterrupt):
                break
            if not user_input.strip():
                continue
            await client.query(user_input)
            async for msg in client.receive_response():
                render_message(msg)
    return 0


# ── Claude backend class ─────────────────────────────────────────────────────


class Claude:
    """Launch Claude Code via the Agent SDK."""

    @staticmethod
    def _build_options(
        *,
        role: str | None = None,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        project_root: Path | None = None,
        tool_config: dict | None = None,
        cwd: Path | str | None = None,
        headless: bool = False,
    ) -> Any:
        """Construct ClaudeAgentOptions for a session."""
        from claude_agent_sdk import (
            ClaudeAgentOptions,
            HookMatcher,
            create_sdk_mcp_server,
        )

        config = tool_config or {}

        # Build allowed tools list — roles get Bash
        allowed = list(_ALLOWED_TOOLS)
        if role:
            allowed.append("Bash")

        # Build MCP tools
        mcp_tools: list = []
        if project_root is not None:
            mcp_tools.append(
                _make_lint_tool(
                    default_select=config.get("ruff_select"),
                    default_ignore=config.get("ruff_ignore"),
                ),
            )
            mcp_tools.append(_make_coderabbit_tool())
            mcp_tools.extend(_make_ticket_tools(project_root, role))

        mcp_servers: dict = {}
        if mcp_tools:
            server = create_sdk_mcp_server(
                "repokit-agent", "1.0.0", tools=mcp_tools,
            )
            mcp_servers["repokit-agent"] = server
            for t in mcp_tools:
                allowed.append(f"mcp__repokit-agent__{t.name}")

        # Build hooks
        hooks = None
        if rules_path is not None and project_root is not None:
            hooks = {
                "PreToolUse": [
                    HookMatcher(
                        matcher="Bash",
                        hooks=[
                            _make_check_bash_hook(rules_path, project_root, role),
                        ],
                    ),
                ],
                "PermissionRequest": [
                    HookMatcher(
                        matcher="^mcp__",
                        hooks=[_make_approve_mcp_hook()],
                    ),
                ],
            }

        # Output format for headless mode
        output_format = None
        if headless and role:
            schema = _OUTPUT_SCHEMAS.get(role)
            if schema is not None:
                output_format = {"type": "json_schema", "schema": schema}

        # System prompt: append role prompt to Claude Code's default
        system_prompt: Any = None
        if role_prompt:
            system_prompt = {
                "type": "preset",
                "preset": "claude_code",
                "append": role_prompt,
            }

        return ClaudeAgentOptions(
            allowed_tools=allowed,
            system_prompt=system_prompt,
            max_turns=config.get("max_turns"),
            output_format=output_format,
            hooks=hooks,
            mcp_servers=mcp_servers,
            cwd=str(cwd) if cwd else None,
            permission_mode="bypassPermissions",
            setting_sources=["project"],
        )

    def run_headless(
        self,
        *,
        prompt: str,
        role: str,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        project_root: Path | None = None,
        tool_config: dict | None = None,
        cwd: Path | str | None = None,
    ) -> tuple[str, int]:
        """Run a headless agent session. Returns (stdout_json, returncode)."""
        options = self._build_options(
            role=role, role_prompt=role_prompt,
            rules_path=rules_path, project_root=project_root,
            tool_config=tool_config, cwd=cwd, headless=True,
        )
        logger.info(f"SDK headless: role={role}")
        return asyncio.run(_run_headless(prompt, options))

    def run_interactive(
        self,
        *,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        project_root: Path | None = None,
        tool_config: dict | None = None,
        cwd: Path | str | None = None,
    ) -> int:
        """Run an interactive agent session. Returns exit code."""
        options = self._build_options(
            role="orchestrator", role_prompt=role_prompt,
            rules_path=rules_path, project_root=project_root,
            tool_config=tool_config, cwd=cwd, headless=False,
        )
        logger.info("SDK interactive session")
        return asyncio.run(_run_interactive(options))
