"""Default BuildTool — runs config command with token expansion."""

from __future__ import annotations

from typing import Any

import click

from .core import RepoTool, TokenFormatter, logger, run_command


class BuildTool(RepoTool):
    name = "build"
    help = "Build the project (runs command from config with token expansion)"

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option("--build-type", "-bt", default=None, help="Build type override")(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {}

    def execute(self, args: dict[str, Any]) -> None:
        command = args.get("command")
        if not command:
            logger.error(
                "No build command configured. Add to config.yaml:\n"
                "  build:\n"
                '    command: "cmake --build {build_dir} --config {build_type}"'
            )
            raise SystemExit(1)

        formatter = TokenFormatter(args)
        resolved = formatter.resolve(command)
        logger.info(f"Running: {resolved}")
        run_command(resolved.split())
