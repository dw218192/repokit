"""Hook factories for agent sessions — no SDK dependency.

These are plain async functions that use the rules engine.
The type annotations reference SDK types only in TYPE_CHECKING
to keep this module importable without the SDK installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..rules import check_command, load_rules

if TYPE_CHECKING:
    from claude_agent_sdk.types import HookContext, HookInput, SyncHookJSONOutput


def _make_check_bash_hook(
    rules_path: Path, project_root: Path, role: str | None,
):
    """Create a PreToolUse hook that checks Bash commands against the rules file."""

    async def check_bash(
        input_data: HookInput, tool_use_id: str | None, context: HookContext,
    ) -> SyncHookJSONOutput:
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
        input_data: HookInput, tool_use_id: str | None, context: HookContext,
    ) -> SyncHookJSONOutput:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }

    return approve_mcp
