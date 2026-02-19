"""CommandRunnerTool — base for tools that run a single configured command."""

from __future__ import annotations

import shlex
from typing import Any

import click

from .core import RepoTool, TokenFormatter, ToolContext, logger, run_command


class CommandRunnerTool(RepoTool):
    """Base for tools that run a single configured command with token expansion."""

    config_hint: str = ""

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option("--build-type", "-bt", default=None, help="Build type override")(cmd)
        return cmd

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        command = args.get("command")
        if not command:
            logger.error(
                f"No {self.name} command configured. Add to config.yaml:\n"
                f"  {self.config_hint}"
            )
            raise SystemExit(1)

        formatter = TokenFormatter({**ctx.tokens, **args})
        resolved = formatter.resolve(command)
        logger.info(f"Running: {resolved}")
        run_command(shlex.split(resolved))
