"""CommandRunnerTool — base for tools that run a single configured command."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import click

from .core import CommandGroup, RepoTool, TokenFormatter, ToolContext, logger, run_command


class CommandRunnerTool(RepoTool):
    """Base for tools that run a single configured command with token expansion.

    Supports ``--dry-run`` to print the resolved command without executing it.
    Dimension tokens (platform, build_type, etc.) are controlled at the group
    level via ``./repo --build-type Release <tool>``, not per-tool flags.
    """

    config_hint: str = ""

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option("--dry-run", is_flag=True, help="Print resolved command without executing")(cmd)
        return cmd

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        command = args.get("command")
        if not command:
            logger.error(
                f"No {self.name!r} command configured. Add to config.yaml:\n"
                f"  {self.config_hint}"
            )
            raise SystemExit(1)

        tokens = dict(ctx.tokens)

        # Merge remaining args as extra tokens (config values, custom fields).
        # Skip tool-framework keys and None values.
        _skip = {"command", "dry_run", "env_script", "cwd"}
        for k, v in args.items():
            if k not in _skip and v is not None:
                tokens[k] = str(v)

        formatter = TokenFormatter(tokens)

        # Resolve env_script and cwd through token expansion.
        env_script: Path | None = None
        cwd: Path | None = None
        raw_env = args.get("env_script")
        if raw_env is not None:
            env_script = Path(formatter.resolve(str(raw_env)))
        raw_cwd = args.get("cwd")
        if raw_cwd is not None:
            cwd = Path(formatter.resolve(str(raw_cwd)))

        if isinstance(command, list):
            resolved_steps = [formatter.resolve(step) for step in command]

            if args.get("dry_run"):
                for i, step in enumerate(resolved_steps, 1):
                    logger.info(f"Would run [{i}/{len(resolved_steps)}]: {step}")
                return

            with CommandGroup(self.name, env_script=env_script, cwd=cwd) as group:
                for step in resolved_steps:
                    group.run(shlex.split(step))
        else:
            resolved = formatter.resolve(command)

            if args.get("dry_run"):
                logger.info(f"Would run: {resolved}")
                return

            logger.info(f"Running: {resolved}")
            run_command(shlex.split(resolved), env_script=env_script, cwd=cwd)
