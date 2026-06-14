"""GenerateTool — `./repo generate`: emit the agent-config surface.

The CLI front door for the generation layer (`generate.py`). On invocation it
re-renders the artifact set and rewrites only what is missing / stale /
hand-edited / version-bumped (GEN-2), gitignores the build output, and reports
what changed. This is the static, in-repo replacement for the old runtime
``_write_plugin`` — repokit *emits* the config surface; it does not *run* a
backend that writes it on the fly (ADR-1).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from ..core import RepoTool, ToolContext, log_section, logger
from . import generate as gen


class GenerateTool(RepoTool):
    name = "generate"
    help = "Generate the agent-config surface (plugin, runner, .mcp.json, settings.json)"

    def setup(self, cmd: click.Command) -> click.Command:
        return click.option(
            "--dry-run", is_flag=True, default=False,
            help="List the artifact targets without writing.",
        )(cmd)

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {"dry_run": False}

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        framework_root = Path(ctx.tokens["framework_root"])

        if args.get("dry_run"):
            gctx = gen.make_context(ctx.workspace_root, framework_root, ctx.config)
            with log_section("Generation targets (dry run)"):
                for art in gen.build_artifacts(gctx):
                    logger.info(f"{art.target}  ({', '.join(art.sources)})")
            return

        result = gen.generate_surface(ctx.workspace_root, framework_root, ctx.config)

        with log_section("Generating agent-config surface"):
            for target in result.written:
                logger.info(f"wrote     {target}")
            for target in result.skipped:
                logger.debug(f"up-to-date {target}")
            for target, reason in result.refused:
                logger.error(f"refused   {target}: {reason}")

        if not result.ok:
            logger.error(
                "Generation refused some targets (see above); resolve the "
                "adoption conflicts and re-run."
            )
            sys.exit(1)

        logger.info(
            f"Generated {len(result.written)} file(s); "
            f"{len(result.skipped)} already up-to-date."
        )
