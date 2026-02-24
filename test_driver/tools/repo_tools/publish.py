"""PublishTool — sync a release branch from main, excluding dev-only files."""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import partial
from pathlib import Path
from typing import Any

import tomllib

import click

from repo_tools.core import RepoTool, ToolContext, logger

# Patterns to exclude from the release branch (matched against tracked paths).
# Override via config publish.exclude list.
DEFAULT_EXCLUDE = [
    r"^test_driver/",
    r"^\.github/",
    r"^\.claude/",
    r"^\.vscode/",
    r"^CLAUDE\.md$",
]

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")


def _git(*args: str, capture: bool = False, cwd: str | Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        text=True,
        capture_output=capture,
        cwd=cwd,
    )
    return result.stdout.strip() if capture else ""


class PublishTool(RepoTool):
    name = "publish"
    help = "Sync release branch from main and tag a version"

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option(
            "--dry-run",
            default=None,
            is_flag=False,
            flag_value=".",
            help="Preview without changes. Optionally pass a directory to populate with the release tree.",
        )(cmd)
        cmd = click.option(
            "--push", is_flag=True, help="Push release branch and tag to origin"
        )(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {"dry_run": None, "push": False}

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        dry_run = args.get("dry_run")
        push = args.get("push", False)

        # All git operations must run from the repo root, not the workspace
        # root (which may be a subdirectory like test_driver/).
        git_root = Path(_git("rev-parse", "--show-toplevel", capture=True))
        git = partial(_git, cwd=git_root)

        # ── Read version from pyproject.toml ──────────────────────
        pyproject_path = git_root / "pyproject.toml"
        if not pyproject_path.exists():
            logger.error("pyproject.toml not found at git root.")
            raise SystemExit(1)

        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        version = pyproject.get("project", {}).get("version", "")
        if not version:
            logger.error("No version found in pyproject.toml [project].version.")
            raise SystemExit(1)

        if not SEMVER_RE.match(version):
            logger.error(f"Version must be semver (e.g. 1.0.0), got '{version}'.")
            raise SystemExit(1)

        tag = f"v{version}"
        logger.info(f"Version: {version} -> tag: {tag}")

        # Already published — skip gracefully, but only if the release branch also
        # contains the tag (handles the case where a previous run pushed the tag
        # but failed to push the release branch).
        ret = subprocess.run(
            ["git", "rev-parse", tag], capture_output=True, text=True, cwd=git_root
        )
        tag_already_exists = ret.returncode == 0
        if tag_already_exists:
            tag_commit = ret.stdout.strip()
            release_has_tag = subprocess.run(
                ["git", "merge-base", "--is-ancestor", tag_commit, "origin/release"],
                capture_output=True,
                cwd=git_root,
            )
            if release_has_tag.returncode == 0:
                logger.info(f"Tag '{tag}' already exists and release branch is up to date. Nothing to do.")
                return
            logger.info(f"Tag '{tag}' exists but release branch is missing it — pushing now.")
            if push:
                git("push", "origin", f"{tag_commit}:refs/heads/release", tag)
                logger.info("Done.")
            else:
                logger.info(f"To publish:  git push origin {tag_commit}:refs/heads/release {tag}")
            return

        # ── Verify preconditions ──────────────────────────────────
        branch = git("symbolic-ref", "--short", "HEAD", capture=True)
        if branch != "main":
            logger.error(f"Must be on 'main' (currently on '{branch}').")
            raise SystemExit(1)

        diff_staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=git_root
        )
        diff_unstaged = subprocess.run(["git", "diff", "--quiet"], cwd=git_root)
        if diff_staged.returncode != 0 or diff_unstaged.returncode != 0:
            logger.error("Working tree or index is dirty.")
            raise SystemExit(1)

        # ── Collect files ─────────────────────────────────────────
        exclude_patterns = ctx.tool_config.get("exclude", DEFAULT_EXCLUDE)
        if exclude_patterns:
            exclude_re = re.compile("|".join(exclude_patterns))
        else:
            exclude_re = None

        all_files = git("ls-files", capture=True).splitlines()
        files = [f for f in all_files if exclude_re is None or not exclude_re.search(f)]

        if not files:
            logger.error("No files remain after exclusions.")
            raise SystemExit(1)

        logger.info(f"Including {len(files)} files in the release branch.")

        if dry_run is not None:
            output_dir = Path(dry_run).resolve() if dry_run != "." else None
            if output_dir:
                if output_dir.exists():
                    shutil.rmtree(output_dir)
                for f in files:
                    dest = output_dir / f
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(git_root / f, dest)
                logger.info(f"Wrote {len(files)} files to {output_dir}")
            else:
                for f in files:
                    logger.info(f"  {f}")
            logger.info(f"Would commit and tag as {tag}.")
            return

        # ── Ensure git identity (CI may not have one) ─────────────
        for key, fallback in [
            ("user.name", "github-actions[bot]"),
            ("user.email", "github-actions[bot]@users.noreply.github.com"),
        ]:
            ret = subprocess.run(
                ["git", "config", key], capture_output=True, text=True, cwd=git_root
            )
            if ret.returncode != 0 or not ret.stdout.strip():
                git("config", key, fallback)

        # ── Ensure release branch exists ──────────────────────────
        ret = subprocess.run(
            ["git", "rev-parse", "--verify", "release"],
            capture_output=True,
            cwd=git_root,
        )
        if ret.returncode != 0:
            logger.info("Creating orphan 'release' branch...")
            git("checkout", "--orphan", "release")
            subprocess.run(["git", "rm", "-rf", "."], capture_output=True, cwd=git_root)
            git("commit", "--allow-empty", "-m", "Initial empty release branch")
            git("checkout", "main")

        # ── Build the release commit ──────────────────────────────
        main_sha = git("rev-parse", "--short", "HEAD", capture=True)
        main_subject = git("log", "-1", "--format=%s", capture=True)

        git("checkout", "release")
        subprocess.run(["git", "rm", "-rf", "."], capture_output=True, cwd=git_root)
        git("checkout", "main", "--", *files)

        if subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=git_root
        ).returncode == 0:
            logger.info("No changes compared to previous release. Nothing to do.")
            git("checkout", "main")
            return

        git("commit", "-m", f"{tag}: {main_subject} (from main {main_sha})")
        git("tag", "-a", tag, "-m", f"Release {tag}")

        git("checkout", "main")

        logger.info(f"Release branch updated and tagged as {tag}.")

        # ── Push ──────────────────────────────────────────────────
        if push:
            logger.info(f"Pushing release branch and {tag}...")
            git("push", "origin", "release", tag)
            logger.info("Done.")
        else:
            logger.info(f"To publish:  git push origin release {tag}")
