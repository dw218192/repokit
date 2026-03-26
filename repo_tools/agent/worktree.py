"""Shared worktree helpers for agent subsystem.

Used by both ``tool.py`` (worktree creation on agent dispatch) and
``tickets.py`` (worktree cleanup on ticket close).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from ..core import _FRAMEWORK_ROOT, logger


def _remove_readonly(func, path, _exc_info):  # noqa: ANN001
    """shutil.rmtree error handler: clear read-only flag and retry."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _force_remove_dir(path: Path, attempts: int = 5, delay: float = 1.0) -> None:
    """Remove a directory tree, handling Windows long paths, read-only and locked files."""
    target = Path(f"\\\\?\\{path.resolve()}") if sys.platform == "win32" else path
    rmtree_kwargs = (
        {"onexc": _remove_readonly}
        if sys.version_info >= (3, 12)
        else {"onerror": _remove_readonly}
    )
    for attempt in range(attempts):
        try:
            shutil.rmtree(target, **rmtree_kwargs)
            return
        except PermissionError:
            if attempt < attempts - 1:
                logger.warning(
                    f"Locked files in {path.name}, "
                    f"retrying in {delay}s ({attempt + 1}/{attempts})"
                )
                time.sleep(delay)
            else:
                raise


def _bootstrap_worktree(wt_dir: Path) -> None:
    """Generate ``./repo`` shims in *wt_dir* so agents can use the CLI.

    Reuses the existing venv from the main workspace — only the shims
    are written, pointing ``--workspace-root`` at the worktree.

    Raises on failure so that dispatch aborts early rather than leaving
    the agent without build/test tooling.
    """
    from .._bootstrap import write_shims
    write_shims(framework_root=_FRAMEWORK_ROOT, workspace_root=wt_dir)
    logger.info(f"Generated repo shims in worktree: {wt_dir}")


def ensure_worktree(
    workspace_root: Path,
    ticket: str,
    *,
    base_ref: str | None = None,
) -> Path:
    """Create or reuse a git worktree for the given ticket under _agent/worktrees/.

    *base_ref* overrides the starting point when creating a new branch
    (defaults to ``HEAD``).
    """
    wt_dir = workspace_root / "_agent" / "worktrees" / ticket
    branch_name = f"worktree-{ticket}"

    if wt_dir.exists():
        logger.info(f"Reusing existing worktree: {wt_dir}")
        return wt_dir

    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=str(workspace_root), capture_output=True,
    )

    branch_exists = subprocess.run(
        ["git", "rev-parse", "--verify", branch_name],
        cwd=str(workspace_root), capture_output=True,
    ).returncode == 0

    if branch_exists:
        subprocess.run(
            ["git", "worktree", "add", str(wt_dir), branch_name],
            cwd=str(workspace_root), check=True,
        )
    else:
        start_point = base_ref or "HEAD"
        subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(wt_dir), start_point],
            cwd=str(workspace_root), check=True,
        )

    _bootstrap_worktree(wt_dir)
    logger.info(f"Created worktree at {wt_dir} (branch: {branch_name})")
    return wt_dir


def remove_worktree(workspace_root: Path, ticket: str) -> None:
    """Remove the git worktree for a ticket."""
    wt_dir = workspace_root / "_agent" / "worktrees" / ticket
    if wt_dir.exists():
        result = subprocess.run(
            ["git", "worktree", "remove", str(wt_dir), "--force"],
            cwd=str(workspace_root), capture_output=True,
        )
        if result.returncode != 0:
            logger.warning(
                f"git worktree remove failed ({result.stderr.decode().strip()}), "
                f"falling back to manual deletion"
            )
            _force_remove_dir(wt_dir)
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(workspace_root), capture_output=True,
            )
        logger.info(f"Removed worktree: {wt_dir}")
    else:
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(workspace_root), capture_output=True,
        )
        logger.info(f"Worktree not found: {wt_dir}")
