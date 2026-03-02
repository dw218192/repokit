"""CleanTool — remove build artifacts and temporary files."""

from __future__ import annotations

import glob as globmod
from pathlib import Path
from typing import Any

import click

from .core import (
    RepoTool,
    TokenFormatter,
    ToolContext,
    log_section,
    logger,
    remove_tree_with_retries,
)


class CleanTool(RepoTool):
    name = "clean"
    help = "Remove build artifacts and temporary files"

    PROTECTED = {".git", "_tools", "_agent", "node_modules"}

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option(
            "--dry-run",
            is_flag=True,
            default=None,
            help="Show what would be removed",
        )(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {
            "dry_run": False,
            "paths": ["{workspace_root}/_build"],
        }

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        formatter = TokenFormatter(ctx.tokens, ctx.config)
        dry_run = bool(args.get("dry_run"))
        paths: list[str] = args.get("paths", [])

        with log_section("Cleaning"):
            removed = 0
            for path_template in paths:
                expanded = formatter.resolve(path_template)
                resolved = Path(expanded)
                if not resolved.is_absolute():
                    resolved = ctx.workspace_root / resolved

                # Safety: must be under workspace_root
                try:
                    resolved.resolve().relative_to(ctx.workspace_root.resolve())
                except ValueError:
                    logger.warning(f"Skipping (outside workspace): {resolved}")
                    continue

                matched = sorted(globmod.glob(str(resolved), recursive=True))
                if not matched:
                    logger.info(f"Not found (skipping): {resolved}")
                    continue

                for match in matched:
                    p = Path(match)
                    try:
                        p.resolve().relative_to(ctx.workspace_root.resolve())
                    except ValueError:
                        logger.warning(f"Skipping (outside workspace): {p}")
                        continue
                    if p.name in self.PROTECTED:
                        logger.warning(f"Skipping (protected): {p}")
                        continue
                    if p.is_dir():
                        if dry_run:
                            logger.info(f"Would remove directory: {p}")
                        else:
                            remove_tree_with_retries(p)
                            logger.info(f"Removed directory: {p}")
                        removed += 1
                    elif p.is_file():
                        if dry_run:
                            logger.info(f"Would remove file: {p}")
                        else:
                            p.unlink()
                            logger.info(f"Removed file: {p}")
                        removed += 1

        action = "Would remove" if dry_run else "Removed"
        logger.info(f"{action} {removed} item(s)")
