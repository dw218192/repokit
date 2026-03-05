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

    from ..coderabbit import TOOL_SCHEMA, call_review as _call_review

    @tool(TOOL_SCHEMA["name"], TOOL_SCHEMA["description"], TOOL_SCHEMA["inputSchema"])
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

    from ..tickets import (
        _ROLE_ALLOWED_TOOLS,
        TOOL_HANDLERS,
        TOOL_SCHEMAS,
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

    for schema in TOOL_SCHEMAS:
        name = schema["name"]
        if name in allowed:
            handler_fn = TOOL_HANDLERS[name]
            tools.append(
                tool(name, schema["description"], schema["inputSchema"])(
                    _mk_handler(handler_fn)
                )
            )

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
    """Run an interactive TUI session."""
    from ..tui import AgentApp

    app = AgentApp(options=options)
    await app.run_async()
    return 0


# ── SDK backend class ────────────────────────────────────────────────────────


class SdkBackend:
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
