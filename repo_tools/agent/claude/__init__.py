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

from ..runner import AgentCLITool
from ...core import posix_path, logger

# Tools that are always pre-approved — all are read-only or local edits.
# Bash is excluded here; it is added per-role and gated by the PreToolUse hook.
_ALLOWED_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch"]

# Roles that get idle-kill tracking (Stop hook + MCP server access).
_ONE_SHOT_ROLES = {"worker", "reviewer"}

# Static plugin manifest written to .claude-plugin/plugin.json.
_PLUGIN_MANIFEST = {"name": "repokit-agent", "version": "1.0.0"}


def _write_plugin(
    plugin_dir: Path,
    rules_path: Path,
    project_root: Path,
    role: str | None = None,
    mcp_port: int | None = None,
) -> None:
    """Write a Claude Code plugin directory with hooks and optional MCP config.

    ``plugin_dir`` is the directory that will be passed to ``--plugin-dir``.
    The layout created is::

        plugin_dir/
        ├── .claude-plugin/
        │   └── plugin.json
        ├── hooks/
        │   └── hooks.json
        └── .mcp.json          (only when MCP servers are needed)
    """
    # -- manifest --
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(_PLUGIN_MANIFEST, indent=2), encoding="utf-8",
    )

    # -- hooks --
    # Build as argument list, serialize once with shlex.join().
    # Uses sys.executable directly — no need for the {repo} token here.
    debug_log = project_root / "_agent" / "hooks.log"
    hook_args = [
        posix_path(sys.executable),
        "-m", "repo_tools.agent.hooks.check_bash",
        "--rules", rules_path.as_posix(),
        "--project-root", project_root.as_posix(),
    ]
    if role:
        hook_args.extend(["--role", role])
    hook_args.extend(["--debug-log", debug_log.as_posix()])

    hook_events: dict = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": shlex.join(hook_args)}],
            }
        ]
    }

    if mcp_port and role in _ONE_SHOT_ROLES:
        stop_args = [
            posix_path(sys.executable),
            "-m", "repo_tools.agent.hooks.stop_hook",
            "--port", str(mcp_port),
        ]
        hook_events["Stop"] = [
            {"hooks": [{"type": "command", "command": shlex.join(stop_args)}]}
        ]

    hooks_dir = plugin_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "hooks.json").write_text(
        json.dumps({"hooks": hook_events}, indent=2), encoding="utf-8",
    )

    # -- MCP servers --
    mcp_path = plugin_dir / ".mcp.json"

    if mcp_port and role in _ONE_SHOT_ROLES:
        mcp_config = {
            "mcpServers": {
                "team": {
                    "type": "http",
                    "url": f"http://127.0.0.1:{mcp_port}",
                },
            }
        }
        mcp_path.write_text(json.dumps(mcp_config, indent=2), encoding="utf-8")
    elif mcp_port is None:
        mcp_config = {
            "mcpServers": {
                "coderabbit": {
                    "type": "stdio",
                    "command": posix_path(sys.executable),
                    "args": ["-m", "repo_tools.agent.hooks.coderabbit_mcp"],
                }
            }
        }
        mcp_path.write_text(json.dumps(mcp_config, indent=2), encoding="utf-8")
    elif mcp_path.exists():
        # Stale .mcp.json from a previous run — remove it
        mcp_path.unlink()


class Claude(AgentCLITool):
    """Launch Claude Code with repo-specific config."""

    def build_command(
        self,
        *,
        role: str | None = None,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        project_root: Path | None = None,
        debug_hooks: bool = False,
        mcp_port: int | None = None,
        cwd: Path | None = None,
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
            plugin_dir = (cwd or project_root) / "_agent" / "plugin"
            _write_plugin(
                plugin_dir, rules_path, project_root,
                role=role, mcp_port=mcp_port,
            )
            cmd.extend(["--plugin-dir", str(plugin_dir)])
        else:
            logger.warning("No rules_path/project_root provided; launching Claude without hooks or MCP server")

        return cmd
