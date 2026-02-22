"""Claude Code backend.

Builds the ``claude`` CLI command with appropriate flags and writes a
plugin directory under ``_agent/plugin/`` so that hooks and MCP servers
are auto-discovered via ``--plugin-dir`` without touching user settings.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from ...core import posix_path, logger

# Tools that are always pre-approved — all are read-only or local edits.
# Bash is excluded here; it is added per-role and gated by the PreToolUse hook.
_ALLOWED_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch"]

# Static plugin manifest written to .claude-plugin/plugin.json.
_PLUGIN_MANIFEST = {"name": "repokit-agent", "version": "1.0.0"}

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
        },
        "required": ["ticket_id", "status", "result", "feedback"],
        "additionalProperties": False,
    },
}


def _write_plugin(
    plugin_dir: Path,
    rules_path: Path,
    project_root: Path,
    role: str | None = None,
) -> None:
    """Write a Claude Code plugin directory with hooks and MCP config.

    ``plugin_dir`` is the directory that will be passed to ``--plugin-dir``.
    The layout created is::

        plugin_dir/
        ├── .claude-plugin/
        │   └── plugin.json
        ├── hooks/
        │   └── hooks.json
        └── .mcp.json
    """
    # -- manifest --
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(_PLUGIN_MANIFEST, indent=2), encoding="utf-8",
    )

    # -- hooks --
    debug_log = project_root / "_agent" / "hooks.log"
    base_cmd = [posix_path(sys.executable), "-m", "repo_tools.agent.hooks"]

    check_bash_args = [
        *base_cmd, "check_bash",
        "--rules", rules_path.as_posix(),
        "--project-root", project_root.as_posix(),
    ]
    if role:
        check_bash_args.extend(["--role", role])
    check_bash_args.extend(["--debug-log", debug_log.as_posix()])

    hook_events: dict = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": shlex.join(check_bash_args)}],
            }
        ]
    }

    hooks_dir = plugin_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "hooks.json").write_text(
        json.dumps({"hooks": hook_events}, indent=2), encoding="utf-8",
    )

    # -- MCP servers --
    mcp_config: dict = {
        "mcpServers": {
            "coderabbit": {
                "type": "stdio",
                "command": posix_path(sys.executable),
                "args": ["-m", "repo_tools.agent.hooks.coderabbit_mcp_stdio"],
            },
            "tickets": {
                "type": "stdio",
                "command": posix_path(sys.executable),
                "args": [
                    "-m", "repo_tools.agent.ticket_mcp",
                    "--project-root", project_root.as_posix(),
                ],
            },
        }
    }
    (plugin_dir / ".mcp.json").write_text(
        json.dumps(mcp_config, indent=2), encoding="utf-8",
    )


class Claude:
    """Launch Claude Code with repo-specific config."""

    def build_command(
        self,
        *,
        prompt: str | None = None,
        role: str | None = None,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        project_root: Path | None = None,
        debug_hooks: bool = False,
        worktree: str | None = None,
        max_turns: int | None = None,
    ) -> list[str]:
        # Build allowed tools list — roles get Bash
        allowed = list(_ALLOWED_TOOLS)
        if role and "Bash" not in allowed:
            allowed.append("Bash")

        cmd = ["claude", "--allowedTools", *allowed]

        if debug_hooks:
            cmd.extend(["-d", "hooks"])

        if role_prompt:
            cmd.extend(["--append-system-prompt", role_prompt])

        # Write plugin directory and add --plugin-dir to the command.
        if (rules_path is not None) != (project_root is not None):
            raise ValueError(
                "rules_path and project_root must both be provided together; "
                f"got rules_path={rules_path!r}, project_root={project_root!r}"
            )
        if rules_path is not None and project_root is not None:
            plugin_dir = project_root / "_agent" / "plugin"
            _write_plugin(plugin_dir, rules_path, project_root, role=role)
            cmd.extend(["--plugin-dir", str(plugin_dir)])
        else:
            logger.warning("No rules_path/project_root provided; launching Claude without hooks or MCP server")

        if worktree:
            cmd.extend(["-w", worktree])

        # Headless mode: add -p with prompt, JSON output, no session persistence
        if prompt is not None:
            cmd.extend(["-p", prompt, "--output-format", "json", "--no-session-persistence"])
            if max_turns is not None:
                cmd.extend(["--max-turns", str(max_turns)])
            schema = _OUTPUT_SCHEMAS.get(role) if role else None
            if schema is not None:
                cmd.extend(["--json-schema", json.dumps(schema)])

        return cmd
