"""PublishTool — sync a release branch from main, excluding dev-only files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import click

from repo_tools.core import RepoTool, ToolContext, logger

# Patterns to exclude from the release branch (matched against tracked paths).
# Override via config publish.exclude list.
DEFAULT_EXCLUDE = [
    r"^test_driver/",
    r"^REFACTOR_PLAN\.md$",
    r"^\.github/",
]

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")


def _git(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        ["git", *args], check=True, text=True, capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def _ensure_git_identity() -> None:
    """Set a default git identity if none is configured (e.g. in CI)."""
    for key, fallback in [
        ("user.name", "github-actions[bot]"),
        ("user.email", "github-actions[bot]@users.noreply.github.com"),
    ]:
        ret = subprocess.run(
            ["git", "config", key], capture_output=True, text=True,
        )
        if ret.returncode != 0 or not ret.stdout.strip():
            _git("config", key, fallback)


class PublishTool(RepoTool):
    name = "publish"
    help = "Sync release branch from main and tag a version (reads VERSION file)"

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option(
            "--dry-run", is_flag=True, help="Show what would happen without making changes"
        )(cmd)
        cmd = click.option(
            "--push", is_flag=True, help="Push release branch and tag to origin"
        )(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {"dry_run": False, "push": False}

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        dry_run = args.get("dry_run", False)
        push = args.get("push", False)

        # Always operate from git root (publish is a repo-level operation).
        git_root = Path(_git("rev-parse", "--show-toplevel", capture=True))

        # ── Read version from VERSION file ────────────────────────
        version_file = git_root / "VERSION"
        if not version_file.exists():
            logger.error("VERSION file not found at git root.")
            raise SystemExit(1)

        version = version_file.read_text().strip()
        if not version:
            logger.error("VERSION file is empty.")
            raise SystemExit(1)

        if not SEMVER_RE.match(version):
            logger.error(f"Version must be semver (e.g. 1.0.0), got '{version}'.")
            raise SystemExit(1)

        tag = f"v{version}"
        logger.info(f"Version: {version} -> tag: {tag}")

        # Already published — skip gracefully.
        ret = subprocess.run(["git", "rev-parse", tag], capture_output=True)
        if ret.returncode == 0:
            logger.info(f"Tag '{tag}' already exists. Nothing to do.")
            return

        # ── Verify preconditions ──────────────────────────────────
        branch = _git("symbolic-ref", "--short", "HEAD", capture=True)
        if branch != "main":
            logger.error(f"Must be on 'main' (currently on '{branch}').")
            raise SystemExit(1)

        diff_staged = subprocess.run(["git", "diff", "--cached", "--quiet"])
        diff_unstaged = subprocess.run(["git", "diff", "--quiet"])
        if diff_staged.returncode != 0 or diff_unstaged.returncode != 0:
            logger.error("Working tree or index is dirty.")
            raise SystemExit(1)

        # ── Collect files ─────────────────────────────────────────
        exclude_patterns = ctx.tool_config.get("exclude", DEFAULT_EXCLUDE)
        if exclude_patterns:
            exclude_re = re.compile("|".join(exclude_patterns))
        else:
            exclude_re = None

        all_files = _git("ls-files", capture=True).splitlines()
        files = [f for f in all_files if exclude_re is None or not exclude_re.search(f)]

        if not files:
            logger.error("No files remain after exclusions.")
            raise SystemExit(1)

        logger.info(f"Including {len(files)} files in the release branch.")

        if dry_run:
            for f in files:
                logger.info(f"  {f}")
            logger.info(f"Would commit and tag as {tag}.")
            return

        _ensure_git_identity()

        # ── Ensure release branch exists ──────────────────────────
        ret = subprocess.run(
            ["git", "rev-parse", "--verify", "release"], capture_output=True
        )
        if ret.returncode != 0:
            logger.info("Creating orphan 'release' branch...")
            _git("checkout", "--orphan", "release")
            subprocess.run(["git", "rm", "-rf", "."], capture_output=True)
            _git("commit", "--allow-empty", "-m", "Initial empty release branch")
            _git("checkout", "main")

        # ── Build the release commit ──────────────────────────────
        main_sha = _git("rev-parse", "--short", "HEAD", capture=True)
        main_subject = _git("log", "-1", "--format=%s", capture=True)

        _git("checkout", "release")
        subprocess.run(["git", "rm", "-rf", "."], capture_output=True)
        _git("checkout", "main", "--", *files)
        _git("add", "-A")

        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
            logger.info("No changes compared to previous release. Nothing to do.")
            _git("checkout", "main")
            return

        _git("commit", "-m", f"{tag}: {main_subject} (from main {main_sha})")
        _git("tag", "-a", tag, "-m", f"Release {tag}")

        _git("checkout", "main")

        logger.info(f"Release branch updated and tagged as {tag}.")

        # ── Push ──────────────────────────────────────────────────
        if push:
            logger.info(f"Pushing release branch and {tag}...")
            _git("push", "origin", "release", tag)
            logger.info("Done.")
        else:
            logger.info(f"To publish:  git push origin release {tag}")
