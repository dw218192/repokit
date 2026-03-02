"""InitTool — install/update project dependencies via uv sync."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from . import _bootstrap
from .core import RepoTool, ToolContext, registered_tool_deps


def _is_local_venv(framework_root: Path) -> bool:
    """True when the running Python lives under framework_root/_managed/venv/.

    Avoids resolve() — venv Python is often a symlink to uv-managed Python
    which would break the path check.
    """
    try:
        Path(sys.executable).relative_to(framework_root / "_managed" / "venv")
        return True
    except ValueError:
        return False


class InitTool(RepoTool):
    name = "init"
    help = "Install/update project dependencies"

    def setup(self, cmd: click.Command) -> click.Command:
        return click.option(
            "--clean", is_flag=True,
            help="Remove generated pyproject and lockfile before reinitializing",
        )(cmd)

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        framework_root = Path(ctx.tokens["framework_root"])
        if not _is_local_venv(framework_root):
            print(
                "ERROR: init refused — the running Python is not in this "
                "framework's _managed/venv/. This usually means "
                "--workspace-root points to a different project. "
                "Bootstrap that project directly instead.",
                file=sys.stderr,
            )
            raise SystemExit(1)

        repo_cfg = ctx.config.get("repo", {})
        if not isinstance(repo_cfg, dict):
            repo_cfg = {}

        if args.get("clean"):
            self._clean(framework_root)

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
    def _clean(framework_root: Path) -> None:
        managed_dir = framework_root / "_managed"
        pyproject = managed_dir / "pyproject.toml"
        lock = managed_dir / "uv.lock"
        for path in (pyproject, lock):
            if path.is_file():
                path.unlink()
                print(f"Removed {path}")
