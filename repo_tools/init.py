"""InitTool — install/update project dependencies via uv sync."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from . import _bootstrap
from .core import RepoTool, ToolContext, registered_tool_deps


class InitTool(RepoTool):
    name = "init"
    help = "Install/update project dependencies"

    def setup(self, cmd: click.Command) -> click.Command:
        return click.option(
            "--clean", is_flag=True,
            help="Remove generated pyproject and lockfile before reinitializing",
        )(cmd)

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        repo_cfg = ctx.config.get("repo", {})
        if not isinstance(repo_cfg, dict):
            repo_cfg = {}

        if args.get("clean"):
            self._clean(ctx.workspace_root)

        extra_deps = repo_cfg.get("extra_deps", [])
        tool_deps = registered_tool_deps()
        all_deps = sorted(set(extra_deps + tool_deps))

        _bootstrap.run(
            framework_root=Path(ctx.tokens["framework_root"]),
            workspace_root=ctx.workspace_root,
            features=repo_cfg.get("features", []),
            tool_deps=all_deps,
        )

    @staticmethod
    def _clean(workspace_root: Path) -> None:
        pyproject = workspace_root / "tools" / "pyproject.toml"
        lock = workspace_root / "tools" / "uv.lock"
        for path in (pyproject, lock):
            if path.is_file():
                path.unlink()
                print(f"Removed {path}")
