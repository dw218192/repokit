"""CLI subprocess backend — wraps the ``claude`` CLI binary.

Writes a plugin directory with hooks and MCP server config, then launches
``claude`` as a subprocess with appropriate flags.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from ...core import logger, posix_path
from ._shared import ALLOWED_TOOLS, OUTPUT_SCHEMAS

# Static plugin manifest written to .claude-plugin/plugin.json.
PLUGIN_MANIFEST = {"name": "repokit-agent", "version": "1.0.0"}


def _write_plugin(
    plugin_dir: Path,
    rules_path: Path,
    project_root: Path,
    role: str | None = None,
    tool_config: dict | None = None,
    project_config: dict | None = None,
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
        json.dumps(PLUGIN_MANIFEST, indent=2), encoding="utf-8",
    )

    # -- hooks --
    config = tool_config or {}
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

    approve_mcp_cmd = shlex.join([
        *base_cmd, "approve_mcp", "--debug-log", debug_log.as_posix(),
    ])

    hook_events: dict = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": shlex.join(check_bash_args)}],
            }
        ],
        "PermissionRequest": [
            {
                "matcher": "^mcp__",
                "hooks": [{"type": "command", "command": approve_mcp_cmd}],
            }
        ],
    }

    # Human ticket review hook
    if bool(config.get("agent", config).get("human_ticket_review")):
        from ..tickets import _load_required_criteria
        required_criteria = _load_required_criteria(config)
        approve_ticket_cmd = shlex.join([
            *base_cmd, "approve_ticket",
            "--required-criteria", json.dumps(required_criteria),
            "--debug-log", debug_log.as_posix(),
        ])
        hook_events["PreToolUse"].append({
            "matcher": "create_ticket$",
            "hooks": [{"type": "command", "command": approve_ticket_cmd}],
        })

    hooks_dir = plugin_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "hooks.json").write_text(
        json.dumps({"hooks": hook_events}, indent=2), encoding="utf-8",
    )

    # -- MCP servers --
    ticket_args = [
        "-m", "repo_tools.agent.mcp.tickets",
        "--project-root", project_root.as_posix(),
    ]
    if role:
        ticket_args.extend(["--role", role])
    lint_args = ["-m", "repo_tools.agent.mcp.lint"]
    if config.get("ruff_select"):
        lint_args.extend(["--select", config["ruff_select"]])
    if config.get("ruff_ignore"):
        lint_args.extend(["--ignore", config["ruff_ignore"]])

    mcp_config: dict = {
        "mcpServers": {
            "coderabbit": {
                "type": "stdio",
                "command": posix_path(sys.executable),
                "args": ["-m", "repo_tools.agent.mcp.coderabbit"],
            },
            "lint": {
                "type": "stdio",
                "command": posix_path(sys.executable),
                "args": lint_args,
            },
            "tickets": {
                "type": "stdio",
                "command": posix_path(sys.executable),
                "args": ticket_args,
            },
        }
    }

    # Repo command tools — all from the tool registry
    from ..repo_cmd import _discover_registered_tools
    registered = _discover_registered_tools()
    if registered:
        repo_cmd_args = [
            "-m", "repo_tools.agent.mcp.repo_cmd",
            "--project-root", project_root.as_posix(),
            "--config", "{}",
            "--extra-tools", json.dumps(registered),
        ]
        mcp_config["mcpServers"]["repo_cmd"] = {
            "type": "stdio",
            "command": posix_path(sys.executable),
            "args": repo_cmd_args,
        }

    # Dispatch tool — orchestrator only
    if role in (None, "orchestrator"):
        mcp_config["mcpServers"]["dispatch"] = {
            "type": "stdio",
            "command": posix_path(sys.executable),
            "args": [
                "-m", "repo_tools.agent.mcp.dispatch",
                "--project-root", project_root.as_posix(),
            ],
        }

    (plugin_dir / ".mcp.json").write_text(
        json.dumps(mcp_config, indent=2), encoding="utf-8",
    )


def _find_claude_cli() -> str:
    """Locate the ``claude`` CLI executable.

    On Windows, ``shutil.which("claude")`` may return a ``.ps1`` PowerShell
    wrapper that ``subprocess.run()`` cannot execute.  npm always generates
    a ``.cmd`` wrapper alongside — prefer that.
    """
    if sys.platform != "win32":
        return "claude"

    found = shutil.which("claude")
    if found is None:
        return "claude"

    if found.lower().endswith(".ps1"):
        cmd_path = found[:-4] + ".cmd"
        if os.path.isfile(cmd_path):
            logger.debug("Using .cmd wrapper: %s", cmd_path)
            return cmd_path
        logger.warning(
            "Found claude.ps1 but no .cmd wrapper; subprocess may fail: %s",
            found,
        )

    return found


class CliBackend:
    """Launch Claude Code via the ``claude`` CLI subprocess."""

    @staticmethod
    def _build_command(
        *,
        prompt: str | None = None,
        role: str | None = None,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        project_root: Path | None = None,
        tool_config: dict | None = None,
        project_config: dict | None = None,
    ) -> list[str]:
        """Build a ``claude`` CLI command list."""
        config = tool_config or {}

        # Build allowed tools list — roles get Bash
        allowed = list(ALLOWED_TOOLS)
        if role and "Bash" not in allowed:
            allowed.append("Bash")

        cmd = [_find_claude_cli(), "--allowedTools", *allowed]

        if config.get("debug_hooks"):
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
            plugin_dir = project_root / "_agent" / (f"plugin-{role}" if role else "plugin")
            _write_plugin(
                plugin_dir, rules_path, project_root,
                role=role, tool_config=config,
                project_config=project_config,
            )
            cmd.extend(["--plugin-dir", str(plugin_dir)])
        else:
            logger.warning("No rules_path/project_root provided; launching Claude without hooks or MCP server")

        # Headless mode: add -p with prompt, JSON output, no session persistence
        if prompt is not None:
            cmd.extend(["-p", prompt, "--output-format", "json", "--no-session-persistence"])
            max_turns = config.get("max_turns")
            if max_turns is not None:
                cmd.extend(["--max-turns", str(max_turns)])
            schema = OUTPUT_SCHEMAS.get(role) if role else None
            if schema is not None:
                cmd.extend(["--json-schema", json.dumps(schema)])

        return cmd

    def run_headless(
        self,
        *,
        prompt: str,
        role: str,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        project_root: Path | None = None,
        tool_config: dict | None = None,
        project_config: dict | None = None,
        cwd: Path | str | None = None,
    ) -> tuple[str, int]:
        """Run a headless agent session. Returns (stdout, returncode)."""
        cmd = self._build_command(
            prompt=prompt, role=role, role_prompt=role_prompt,
            rules_path=rules_path, project_root=project_root,
            tool_config=tool_config, project_config=project_config,
        )
        logger.info(f"CLI headless: {cmd[0]} (role={role})")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
        if proc.returncode != 0 and proc.stderr:
            logger.warning(f"claude-cli stderr: {proc.stderr.strip()}")
        return (proc.stdout, proc.returncode)

    def run_interactive(
        self,
        *,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        project_root: Path | None = None,
        tool_config: dict | None = None,
        project_config: dict | None = None,
        cwd: Path | str | None = None,
        initial_prompt: str | None = None,
        resume: str | None = None,
    ) -> tuple[int, str | None]:
        """Run an interactive agent session. Returns (exit_code, session_id)."""
        cmd = self._build_command(
            role="orchestrator", role_prompt=role_prompt,
            rules_path=rules_path, project_root=project_root,
            tool_config=tool_config, project_config=project_config,
        )
        if resume:
            cmd.extend(["--resume", resume])
        if initial_prompt:
            cmd.extend(["-p", initial_prompt])
        logger.info("CLI interactive session")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
        # CLI doesn't expose session_id in interactive mode
        return (proc.returncode, None)
