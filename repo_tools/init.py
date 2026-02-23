"""InitTool — install/update project dependencies."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .core import RepoTool, ToolContext, find_venv_executable, logger
from .gitignore import patch_gitignore


def _find_uv(workspace_root: Path) -> str | None:
    """Locate the uv executable: _tools/bin first, then venv, then PATH."""
    suffix = ".exe" if sys.platform == "win32" else ""

    tools_bin = workspace_root / "_tools" / "bin" / f"uv{suffix}"
    if tools_bin.exists():
        return str(tools_bin)

    venv_uv = find_venv_executable("uv")
    if shutil.which(venv_uv):
        return venv_uv

    return None


def _pip_install(uv: str, requirements: Path) -> bool:
    """Run ``uv pip install`` for a single requirements file. Returns True on success."""
    cmd = [uv, "pip", "install", "--python", sys.executable, "-r", str(requirements)]
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd)
    return result.returncode == 0


class InitTool(RepoTool):
    name = "init"
    help = "Install/update project dependencies"

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        uv = _find_uv(ctx.workspace_root)
        if uv is None:
            logger.error("uv not found — run bootstrap first")
            sys.exit(1)

        framework_root = Path(ctx.tokens["framework_root"])
        framework_reqs = framework_root / "requirements.txt"
        project_reqs = ctx.workspace_root / "tools" / "requirements.txt"

        ok = True

        if framework_reqs.exists():
            logger.info("Installing framework dependencies …")
            if not _pip_install(uv, framework_reqs):
                logger.error("Framework dependency install failed")
                ok = False
        else:
            logger.warning("No framework requirements.txt found at %s", framework_reqs)

        if project_reqs.exists():
            logger.info("Installing project dependencies …")
            if not _pip_install(uv, project_reqs):
                logger.error("Project dependency install failed")
                ok = False

        if not ok:
            sys.exit(1)

        logger.info("Dependencies up to date")

        logger.info("Patching .gitignore …")
        patch_gitignore(ctx.workspace_root / ".gitignore")
