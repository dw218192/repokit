"""Default TestTool — runs config command with token expansion."""

from __future__ import annotations

from typing import Any

import click

from .command_runner import CommandRunnerTool
from .core import ToolContext


class TestTool(CommandRunnerTool):
    name = "test"
    help = "Run tests (runs command from config with token expansion)"
    config_hint = 'test:\n    command: "ctest --test-dir {build_dir} --build-config {build_type}"'

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = super().setup(cmd)
        cmd = click.option("-v", "--verbose", is_flag=True, help="Verbose test output")(cmd)
        return cmd

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        if args.get("verbose") and args.get("command"):
            args["command"] += " " + args.get("verbose_flag", "--output-on-failure")
        super().execute(ctx, args)
