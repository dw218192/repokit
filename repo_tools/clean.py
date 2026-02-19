"""CleanTool — generic artifact cleanup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from .core import RepoTool, ToolContext, logger, remove_tree_with_retries


class CleanTool(RepoTool):
    name = "clean"
    help = "Remove build artifacts and caches"

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option("--build", "clean_build", is_flag=True, help="Remove build output for current platform/type")(cmd)
        cmd = click.option("--logs", "clean_logs", is_flag=True, help="Remove log files")(cmd)
        cmd = click.option("--all", "clean_all", is_flag=True, help="Remove everything (build root, logs)")(cmd)
        cmd = click.option("--dry-run", is_flag=True, help="Show what would be removed")(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {
            "clean_build": False,
            "clean_logs": False,
            "clean_all": False,
            "dry_run": False,
        }

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        build_root = Path(ctx.tokens.get("build_root", str(ctx.workspace_root / "_build")))
        build_dir = Path(ctx.tokens.get("build_dir", str(build_root / "default" / "Debug")))
        logs_root = Path(ctx.tokens.get("logs_root", str(ctx.workspace_root / "_logs")))
        dry_run = args.get("dry_run", False)

        targets: list[Path] = []

        if args.get("clean_all"):
            targets.append(build_root)
            targets.append(logs_root)
        else:
            if args.get("clean_build"):
                targets.append(build_dir)
            if args.get("clean_logs"):
                targets.append(logs_root)

        # De-duplicate
        seen: set[Path] = set()
        unique: list[Path] = []
        for t in targets:
            resolved = t.resolve()
            if resolved not in seen:
                seen.add(resolved)
                unique.append(t)

        if not unique:
            logger.info("Nothing to clean. Use --build, --logs, or --all.")
            return

        for target in unique:
            if not target.exists():
                logger.info(f"Skip (not found): {target}")
                continue
            if dry_run:
                logger.info(f"Would remove: {target}")
            else:
                logger.info(f"Removing: {target}")
                if target.is_file():
                    target.unlink()
                else:
                    remove_tree_with_retries(target)

        if dry_run:
            logger.info("Dry run complete. No files were removed.")
        else:
            logger.info("Clean complete.")
