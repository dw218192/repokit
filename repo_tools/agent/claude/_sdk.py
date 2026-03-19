"""SDK backend — uses claude-agent-sdk for in-process sessions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ...core import logger
from ._hooks import _make_approve_mcp_hook, _make_check_bash_hook
from ._shared import ALLOWED_TOOLS, OUTPUT_SCHEMAS


# ── In-process MCP tools ─────────────────────────────────────────────────────


def _make_lint_tool(
    default_select: str | None = None,
    default_ignore: str | None = None,
):
    """Create a lint MCP tool backed by call_lint()."""
    from claude_agent_sdk import tool

    from ..lint import TOOL_SCHEMA, call_lint as _call_lint

    @tool(TOOL_SCHEMA["name"], TOOL_SCHEMA["description"], TOOL_SCHEMA["inputSchema"])
    async def lint_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = await asyncio.to_thread(
            _call_lint, args,
            default_select=default_select, default_ignore=default_ignore,
        )
        return {
            "content": [{"type": "text", "text": result.get("text", "")}],
            **({"is_error": True} if result.get("isError") else {}),
        }

    return lint_tool


def _make_coderabbit_tool():
    """Create a coderabbit_review MCP tool backed by call_review()."""
    from claude_agent_sdk import tool

    from ..coderabbit import TOOL_SCHEMA, call_review as _call_review

    @tool(TOOL_SCHEMA["name"], TOOL_SCHEMA["description"], TOOL_SCHEMA["inputSchema"])
    async def coderabbit_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = await asyncio.to_thread(_call_review, args)
        return {
            "content": [{"type": "text", "text": result.get("text", "")}],
            **({"is_error": True} if result.get("isError") else {}),
        }

    return coderabbit_tool


def _make_ticket_tools(workspace_root: Path, role: str | None, config: dict | None = None):
    """Create ticket MCP tools filtered by role."""
    from claude_agent_sdk import tool

    from ..tickets import (
        _ROLE_ALLOWED_TOOLS,
        TOOL_HANDLERS,
        TOOL_SCHEMAS,
    )

    allowed = _ROLE_ALLOWED_TOOLS.get(role or "orchestrator", set())
    tools = []

    def _mk_handler(name, fn):
        extra = {"config": config or {}} if name == "create_ticket" else {}
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            result = await asyncio.to_thread(fn, workspace_root, args, role=role, **extra)
            return {
                "content": [{"type": "text", "text": result.get("text", "")}],
                **({"is_error": True} if result.get("isError") else {}),
            }
        return handler

    for schema in TOOL_SCHEMAS:
        name = schema["name"]
        if name in allowed:
            handler_fn = TOOL_HANDLERS[name]
            tools.append(
                tool(name, schema["description"], schema["inputSchema"])(
                    _mk_handler(name, handler_fn)
                )
            )

    return tools


def _make_repo_run_tool(workspace_root: Path):
    """Create a single ``repo_run`` MCP tool for all registered repo commands."""
    from claude_agent_sdk import tool

    from ..repo_cmd import _discover_registered_tools, call_repo_run

    all_cmds = _discover_registered_tools()
    if not all_cmds:
        return None

    known = {c["name"] for c in all_cmds}
    cmd_lines = "\n".join(f"- {c['name']}: {c['description']}" for c in all_cmds)
    description = f"Run a repo command.\n\nAvailable commands:\n{cmd_lines}"
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": sorted(known),
                "description": "Command to run",
            },
            "extra_args": {
                "type": "string",
                "default": "",
                "description": "Additional CLI arguments",
            },
        },
        "required": ["command"],
    }

    @tool("repo_run", description, input_schema)
    async def repo_run_tool(args: dict[str, Any]) -> dict[str, Any]:
        command = args.get("command", "")
        if command not in known:
            return {
                "content": [{"type": "text", "text": f"Unknown command: {command!r}"}],
                "is_error": True,
            }
        result = await asyncio.to_thread(
            call_repo_run, command, args, workspace_root=workspace_root,
        )
        return {
            "content": [{"type": "text", "text": result.get("text", "")}],
            **({"is_error": True} if result.get("isError") else {}),
        }

    return repo_run_tool


def _make_dispatch_tool(workspace_root: Path):
    """Create dispatch_agent MCP tool for orchestrator sessions."""
    from claude_agent_sdk import tool

    from ..dispatch import TOOL_SCHEMA, call_dispatch as _call_dispatch

    @tool(TOOL_SCHEMA["name"], TOOL_SCHEMA["description"], TOOL_SCHEMA["inputSchema"])
    async def dispatch_tool(args: dict[str, Any]) -> dict[str, Any]:
        result = await asyncio.to_thread(
            _call_dispatch, args, workspace_root=workspace_root,
        )
        return {
            "content": [{"type": "text", "text": result.get("text", "")}],
            **({"is_error": True} if result.get("isError") else {}),
        }

    return dispatch_tool


def _make_event_tools(config: dict):
    """Create list_events and subscribe_event MCP tools."""
    from claude_agent_sdk import tool

    from ..events import TOOL_SCHEMAS, list_events_text, subscribe

    tools = []

    list_schema = TOOL_SCHEMAS[0]

    @tool(list_schema["name"], list_schema["description"], list_schema["inputSchema"])
    async def list_events_tool(args: dict[str, Any]) -> dict[str, Any]:
        text = list_events_text(config)
        return {"content": [{"type": "text", "text": text}]}

    tools.append(list_events_tool)

    sub_schema = TOOL_SCHEMAS[1]

    @tool(sub_schema["name"], sub_schema["description"], sub_schema["inputSchema"])
    async def subscribe_event_tool(args: dict[str, Any]) -> dict[str, Any]:
        group = args.get("group", "")
        event = args.get("event", "")
        tokens = args.get("tokens", {})
        try:
            msg = subscribe(group, event, config, tokens)
        except KeyError as exc:
            return {
                "content": [{"type": "text", "text": str(exc)}],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": msg}]}

    tools.append(subscribe_event_tool)
    return tools


# ── Async implementations ────────────────────────────────────────────────────


async def _run_headless(
    prompt: str,
    options: Any,
) -> tuple[str, int, str | None]:
    """Run a headless query and return (stdout_json, returncode, session_id)."""
    from claude_agent_sdk import ProcessError, ResultMessage, query

    result_msg = None
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, ResultMessage):
                result_msg = msg
    except ProcessError as exc:
        envelope = {"type": "result", "subtype": "error", "is_error": True}
        return (json.dumps(envelope), exc.exit_code or 1, None)

    if result_msg is None:
        envelope = {"type": "result", "subtype": "error", "is_error": True}
        return (json.dumps(envelope), 1, None)

    session_id = result_msg.session_id
    envelope = {
        "type": "result",
        "subtype": result_msg.subtype,
        "is_error": result_msg.is_error,
        "structured_output": result_msg.structured_output,
    }
    return (json.dumps(envelope), 1 if result_msg.is_error else 0, session_id)


async def _run_interactive(
    options: Any,
    initial_prompt: str | None = None,
    resume: str | None = None,
    tools_metadata: list[dict[str, str]] | None = None,
    human_ticket_review: bool = False,
    required_criteria: list[str] | None = None,
) -> tuple[int, str | None]:
    """Run an interactive TUI session. Returns (exit_code, session_id)."""
    from ..tui import AgentApp

    app = AgentApp(
        options=options, initial_prompt=initial_prompt, resume=resume,
        tools_metadata=tools_metadata or [],
        human_ticket_review=human_ticket_review,
        required_criteria=required_criteria or [],
    )
    await app.run_async()
    return (0, app._session_id)


# ── SDK backend class ────────────────────────────────────────────────────────


class SdkBackend:
    """Launch Claude Code via the Agent SDK."""

    @staticmethod
    def _build_options(
        *,
        role: str | None = None,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        extra_rules_paths: list[Path] | None = None,
        project_root: Path | None = None,
        tool_config: dict | None = None,
        project_config: dict | None = None,
        cwd: Path | str | None = None,
        headless: bool = False,
    ) -> tuple[Any, list[dict[str, str]]]:
        """Construct ClaudeAgentOptions and tool metadata for a session."""
        from claude_agent_sdk import (
            ClaudeAgentOptions,
            HookMatcher,
            create_sdk_mcp_server,
        )

        config = tool_config or {}

        # Build allowed tools list — roles get Bash
        allowed = list(ALLOWED_TOOLS)
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
            mcp_tools.extend(_make_ticket_tools(project_root, role, config=config))
            repo_tool = _make_repo_run_tool(project_root)
            if repo_tool is not None:
                mcp_tools.append(repo_tool)

            # Orchestrator-only tools (interactive sessions)
            if not headless:
                mcp_tools.append(_make_dispatch_tool(project_root))
                events_cfg = config.get("agent", config).get("events")
                if events_cfg:
                    mcp_tools.extend(_make_event_tools(config))

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
                            _make_check_bash_hook(rules_path, project_root, role, extra_rules_paths),
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
            schema = OUTPUT_SCHEMAS.get(role)
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

        def _on_stderr(line: str) -> None:
            logger.warning("claude-cli stderr: %s", line)

        opts_kwargs: dict[str, Any] = dict(
            allowed_tools=allowed,
            system_prompt=system_prompt,
            max_turns=config.get("max_turns"),
            output_format=output_format,
            hooks=hooks,
            mcp_servers=mcp_servers,
            cwd=str(cwd) if cwd else None,
            permission_mode="bypassPermissions",
            setting_sources=["project"],
            stderr=_on_stderr,
        )

        # Tool metadata for the TUI's Available Tools pane
        tools_meta = [
            {"name": name, "description": "", "group": "Built-in"}
            for name in allowed if not name.startswith("mcp__")
        ]
        for t in mcp_tools:
            tools_meta.append({
                "name": t.name,
                "description": t.description,
                "group": "MCP",
            })

        return (ClaudeAgentOptions(**opts_kwargs), tools_meta)

    def run_headless(
        self,
        *,
        prompt: str,
        role: str,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        extra_rules_paths: list[Path] | None = None,
        project_root: Path | None = None,
        tool_config: dict | None = None,
        project_config: dict | None = None,
        cwd: Path | str | None = None,
    ) -> tuple[str, int]:
        """Run a headless agent session. Returns (stdout_json, returncode)."""
        options, _meta = self._build_options(
            role=role, role_prompt=role_prompt,
            rules_path=rules_path, extra_rules_paths=extra_rules_paths,
            project_root=project_root,
            tool_config=tool_config, project_config=project_config,
            cwd=cwd, headless=True,
        )
        logger.info(f"SDK headless: role={role}")
        stdout, rc, _session_id = asyncio.run(
            _run_headless(prompt, options),
        )
        return (stdout, rc)

    def run_interactive(
        self,
        *,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        extra_rules_paths: list[Path] | None = None,
        project_root: Path | None = None,
        tool_config: dict | None = None,
        project_config: dict | None = None,
        cwd: Path | str | None = None,
        initial_prompt: str | None = None,
        resume: str | None = None,
    ) -> tuple[int, str | None]:
        """Run an interactive agent session. Returns (exit_code, session_id)."""
        options, tools_meta = self._build_options(
            role="orchestrator", role_prompt=role_prompt,
            rules_path=rules_path, extra_rules_paths=extra_rules_paths,
            project_root=project_root,
            tool_config=tool_config, project_config=project_config,
            cwd=cwd, headless=False,
        )
        config = tool_config or {}
        human_ticket_review = bool(
            config.get("agent", config).get("human_ticket_review"),
        )
        from ..tickets import _load_required_criteria
        required_criteria = _load_required_criteria(config)
        logger.info("SDK interactive session")
        return asyncio.run(
            _run_interactive(
                options, initial_prompt,
                resume=resume, tools_metadata=tools_meta,
                human_ticket_review=human_ticket_review,
                required_criteria=required_criteria,
            ),
        )
