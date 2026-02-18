"""ContextTool — display resolved tokens."""

from __future__ import annotations

import json
from typing import Any

import click

from .core import RepoTool, logger


class ContextTool(RepoTool):
    name = "context"
    help = "Display resolved tokens (paths, platform, extensions, etc.)"

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option("--json", "as_json", is_flag=True, help="Output as JSON")(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {"as_json": False}

    def execute(self, args: dict[str, Any]) -> None:
        # Filter to only show meaningful tokens (not the full args dict)
        skip_keys = {"passthrough_args", "as_json", "command", "backends"}
        tokens = {k: v for k, v in sorted(args.items()) if k not in skip_keys and isinstance(v, str)}

        if args.get("as_json"):
            print(json.dumps(tokens, indent=2))
        else:
            for key, value in tokens.items():
                logger.info(f"{key}: {value}")
