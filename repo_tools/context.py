"""ContextTool — display resolved tokens."""

from __future__ import annotations

import json
from typing import Any

import click

from .core import RepoTool, ToolContext, logger


class ContextTool(RepoTool):
    name = "context"
    help = "Display resolved tokens (paths, platform, extensions, etc.)"

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option("--json", "as_json", is_flag=True, help="Output as JSON")(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {"as_json": False}

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        tokens = dict(sorted(ctx.tokens.items()))

        if args.get("as_json"):
            print(json.dumps(tokens, indent=2))
        else:
            for key, value in tokens.items():
                logger.info(f"{key}: {value}")
