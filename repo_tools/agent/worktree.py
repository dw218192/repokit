"""Shared worktree helpers for agent subsystem.

Used by both ``tool.py`` (worktree creation on agent dispatch) and
``ticket_mcp.py`` (worktree cleanup on ticket close).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..core import logger


def ensure_worktree(workspace_root: Path, ticket: str) -> Path:
    """Create or reuse a git worktree for the given ticket under _agent/worktrees/."""
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
        subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(wt_dir), "HEAD"],
            cwd=str(workspace_root), check=True,
        )

    logger.info(f"Created worktree at {wt_dir} (branch: {branch_name})")
    return wt_dir


def remove_worktree(workspace_root: Path, ticket: str) -> None:
    """Remove the git worktree for a ticket."""
    wt_dir = workspace_root / "_agent" / "worktrees" / ticket
    if wt_dir.exists():
        subprocess.run(
            ["git", "worktree", "remove", str(wt_dir), "--force"],
            cwd=str(workspace_root), check=True,
        )
        logger.info(f"Removed worktree: {wt_dir}")
    else:
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(workspace_root), capture_output=True,
        )
        logger.info(f"Worktree not found: {wt_dir}")
