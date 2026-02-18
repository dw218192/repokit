"""Agent tool - launches coding agents with repo-specific config."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import click

from ..core import RepoTool, logger
from .approver import AutoApprover
from .claude import Claude
from .runner import AgentCLITool
from .wezterm import PaneSession, ensure_installed

BACKENDS: dict[str, AgentCLITool] = {
    "claude": Claude(),
}


class AgentTool(RepoTool):
    name = "agent"
    help = "Run coding agents with workflows tailored for this repository."

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option(
            "--backend",
            type=click.Choice(list(BACKENDS.keys())),
            default="claude",
            help="Agent backend to use (default: claude)",
        )(cmd)
        cmd = click.option(
            "--auto-approve",
            is_flag=True,
            default=False,
            help="Auto-approve tool permissions that match rules.toml",
        )(cmd)
        cmd = click.argument("subcommand", default="run")(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {}

    def execute(self, args: dict[str, Any]) -> None:
        subcommand = args.get("subcommand", "run")
        if subcommand != "run":
            logger.error(f"Unknown agent subcommand: {subcommand}")
            raise SystemExit(1)

        ensure_installed()
        backend_name = args.get("backend", "claude")
        backend = BACKENDS[backend_name]
        cwd = args.get("workspace_root")
        cmd = backend.build_command(cwd=cwd)

        session = PaneSession.spawn(cmd, cwd=cwd)
        if session is None:
            logger.error("Failed to obtain WezTerm pane.")
            sys.exit(1)

        logger.info(f"{backend_name} running in WezTerm pane {session.pane_id}")

        if not args.get("auto_approve", False):
            return

        # Find rules file: project-specific first, then framework default
        rules_file = None
        if cwd:
            project_rules = Path(cwd) / "tools" / "agent" / "rules.toml"
            if project_rules.exists():
                rules_file = project_rules

        if rules_file is None:
            rules_file = Path(__file__).parent / "rules_default.toml"

        approver = AutoApprover(
            backend, session, rules_file,
            project_root=Path(cwd) if cwd else None,
        )
        approver.start()

        try:
            while session.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Interrupted.")
        finally:
            approver.stop()
            session.kill()
            logger.info("Session closed.")
