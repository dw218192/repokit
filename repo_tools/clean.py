"""CleanTool — remove build artifacts and temporary files."""

from __future__ import annotations

import glob as globmod
import os
import re
import sys
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

_RE_PREFIX = "re:"


class CleanTool(RepoTool):
    name = "clean"
    help = "Remove build artifacts and temporary files"

    PROTECTED = {".git", "_agent"}

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.argument("group_names", nargs=-1)(cmd)
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
            "paths": [],
            "groups": {},
        }

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        formatter = TokenFormatter(ctx.tokens, ctx.config)
        dry_run = bool(args.get("dry_run"))
        groups: dict[str, list[str]] = dict(args.get("groups", {}))
        flat_paths: list[str] = list(args.get("paths", []))
        requested: tuple[str, ...] = args.get("group_names", ())

        if requested:
            patterns: list[str] = []
            for name in requested:
                if name not in groups:
                    available = ", ".join(sorted(groups)) if groups else "(none)"
                    logger.error(f"Unknown clean group: '{name}'. Available: {available}")
                    sys.exit(1)
                patterns.extend(groups[name])
        else:
            patterns = flat_paths[:]
            for group_paths in groups.values():
                patterns.extend(group_paths)

        # Split into regex vs glob patterns
        regex_patterns: list[re.Pattern[str]] = []
        glob_patterns: list[str] = []
        for pat in patterns:
            if pat.startswith(_RE_PREFIX):
                regex_patterns.append(re.compile(pat[len(_RE_PREFIX):]))
            else:
                glob_patterns.append(pat)

        with log_section("Cleaning"):
            removed = 0
            removed += self._clean_globs(ctx, formatter, glob_patterns, dry_run)
            removed += self._clean_regex(ctx, regex_patterns, dry_run)

        action = "Would remove" if dry_run else "Removed"
        logger.info(f"{action} {removed} item(s)")

    def _clean_globs(
        self,
        ctx: ToolContext,
        formatter: TokenFormatter,
        patterns: list[str],
        dry_run: bool,
    ) -> int:
        removed = 0
        for path_template in patterns:
            expanded = formatter.resolve(path_template)
            resolved = Path(expanded)
            if not resolved.is_absolute():
                resolved = ctx.workspace_root / resolved

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
                removed += self._try_remove(ctx, Path(match), dry_run)
        return removed

    def _clean_regex(
        self,
        ctx: ToolContext,
        patterns: list[re.Pattern[str]],
        dry_run: bool,
    ) -> int:
        if not patterns:
            return 0
        removed = 0
        root = ctx.workspace_root.resolve()
        for dirpath, dirnames, filenames in os.walk(root):
            # Use forward slashes for consistent matching
            rel = Path(dirpath).resolve().relative_to(root).as_posix()
            for pat in patterns:
                if pat.search(rel):
                    removed += self._try_remove(ctx, Path(dirpath), dry_run)
                    dirnames.clear()  # don't descend into removed dirs
                    break
            # Also check files
            else:
                for fname in filenames:
                    file_rel = f"{rel}/{fname}" if rel != "." else fname
                    for pat in patterns:
                        if pat.search(file_rel):
                            removed += self._try_remove(
                                ctx, Path(dirpath) / fname, dry_run
                            )
                            break
        return removed

    def _try_remove(self, ctx: ToolContext, p: Path, dry_run: bool) -> int:
        try:
            p.resolve().relative_to(ctx.workspace_root.resolve())
        except ValueError:
            logger.warning(f"Skipping (outside workspace): {p}")
            return 0
        if p.name in self.PROTECTED:
            logger.warning(f"Skipping (protected): {p}")
            return 0
        if p.is_dir():
            if dry_run:
                logger.info(f"Would remove directory: {p}")
            else:
                remove_tree_with_retries(p)
                logger.info(f"Removed directory: {p}")
            return 1
        elif p.is_file():
            if dry_run:
                logger.info(f"Would remove file: {p}")
            else:
                p.unlink()
                logger.info(f"Removed file: {p}")
            return 1
        return 0
