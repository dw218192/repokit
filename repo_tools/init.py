"""InitTool — install/update project dependencies via uv sync."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import click

from . import _bootstrap
from .core import RepoTool, ToolContext, _TOOL_REGISTRY, get_config_file, registered_tool_deps


_NON_TOOL_SECTIONS = {"repo", "agent"}


def _is_repokit_config(path: Path) -> bool:
    """Detect whether an existing YAML file is a repokit config.

    Checks if any top-level keys match known repokit sections
    (registered tool names + repo/agent).
    """
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    known_keys = {t.name for t in _TOOL_REGISTRY.values()} | _NON_TOOL_SECTIONS
    return bool(data.keys() & known_keys)


def _is_local_venv(framework_root: Path) -> bool:
    """True when the running Python belongs to framework_root/_managed/venv/.

    Uses os.path.realpath() to resolve all symlinks on both sides — this
    handles two layers of indirection that break naive path checks:
      1. The framework dir itself may be a symlink (CI junction/symlink).
      2. The venv python may be a symlink to uv-managed Python (common on
         Linux), so sys.executable resolves outside the venv dir.
    Comparing sys.prefix (the venv dir Python detected at startup) avoids
    both problems.
    """
    venv = framework_root / "_managed" / "venv"
    if not (venv / "pyvenv.cfg").is_file():
        return False
    return os.path.realpath(sys.prefix) == os.path.realpath(str(venv))


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

        self._generate_config_template(ctx.workspace_root, framework_root)
        self._generate_ci_template(ctx.workspace_root)
        self._generate_claude_template(ctx.workspace_root, framework_root)

    @staticmethod
    def _generate_config_template(workspace_root: Path, framework_root: Path) -> None:
        config_filename = get_config_file(str(workspace_root))

        # Explicit override — existence alone is sufficient to skip
        if config_filename != "config.yaml":
            config_path = workspace_root / config_filename
            if config_path.exists():
                print(f"Config file found: {config_filename}, skipping template generation")
                return
            config_path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
            print(f"Generated config template: {config_filename}")
            return

        # Default case — need _is_repokit_config to distinguish from foreign configs
        default_path = workspace_root / "config.yaml"
        if not default_path.exists():
            default_path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
            print("Generated config template: config.yaml")
        elif _is_repokit_config(default_path):
            print("Config file found: config.yaml, skipping template generation")
        else:
            # Foreign config.yaml exists — prompt for an alternate name
            alt_name = click.prompt(
                "config.yaml already exists. Enter config filename for repokit",
                default="repokit.yaml",
            )
            (workspace_root / alt_name).write_text(_CONFIG_TEMPLATE, encoding="utf-8")
            print(f"Generated config template: {alt_name}")
            # Persist the override so get_config_file() picks it up
            config_name_path = framework_root / "_managed" / "config_name"
            config_name_path.parent.mkdir(parents=True, exist_ok=True)
            config_name_path.write_text(alt_name, encoding="utf-8")

    @staticmethod
    def _generate_ci_template(workspace_root: Path) -> None:
        ci_path = workspace_root / ".github" / "workflows" / "ci.yml"
        if ci_path.exists():
            print("CI workflow found: .github/workflows/ci.yml, skipping template generation")
            return
        ci_path.parent.mkdir(parents=True, exist_ok=True)
        ci_path.write_text(_CI_TEMPLATE, encoding="utf-8")
        print("Generated CI template: .github/workflows/ci.yml")

    @staticmethod
    def _generate_claude_template(workspace_root: Path, framework_root: Path) -> None:
        claude_path = workspace_root / "CLAUDE.md"
        framework_rel = os.path.relpath(
            str(framework_root), str(workspace_root),
        ).replace("\\", "/")
        section = _CLAUDE_TEMPLATE.format(framework_rel=framework_rel)

        if not claude_path.exists():
            claude_path.write_text(section, encoding="utf-8")
            print("Generated CLAUDE.md with repokit section")
            return

        existing = claude_path.read_text(encoding="utf-8")
        if "## Repo tooling" in existing:
            print("CLAUDE.md already has repokit section, skipping")
            return

        separator = "" if existing.endswith("\n") else "\n"
        claude_path.write_text(existing + separator + "\n" + section, encoding="utf-8")
        print("Appended repokit section to CLAUDE.md")

    @staticmethod
    def _clean(framework_root: Path) -> None:
        managed_dir = framework_root / "_managed"
        pyproject = managed_dir / "pyproject.toml"
        lock = managed_dir / "uv.lock"
        for path in (pyproject, lock):
            if path.is_file():
                path.unlink()
                print(f"Removed {path}")


_CONFIG_TEMPLATE = """\
# ── Repo section ──────────────────────────────────────────────────────
# repo:
#   tokens:
#     my_token: "value"           # custom token usable as {my_token}
#   extra_deps:
#     - "requests>=2.0"           # additional pip dependencies
#   features:
#     - python                    # enable feature groups (python, cpp, ...)

# ── Build / Test / Format ─────────────────────────────────────────────
# test:
#   steps:
#     - run: "{repo} python -m pytest tests/"

# build:
#   steps:
#     - run: "echo build step here"

# format:
#   paths:
#     - "src/"
#     - "tests/"

# ── Agent section ─────────────────────────────────────────────────────
# agent:
#   backend: sdk                  # sdk or cli
#   human_ticket_review: true          # require user approval before creating tickets
#   prompts:
#     system: "prompts/system.md"
#   required_criteria:
#     - "All existing tests still pass"
#   allowlist:
#     - "pytest"
"""

_CI_TEMPLATE = """\
name: CI

on: [push, pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: true

      - name: Bootstrap
        run: tools/framework/bootstrap.sh

      - name: Build
        run: ./repo build

      - name: Test
        run: ./repo test
"""

_CLAUDE_TEMPLATE = """\
## Repo tooling

This project uses [repokit]({framework_rel}/README.md) for general project tooling (e.g. build, test, format).

- **CLI**: `./repo <command>` (or `repo.cmd` on Windows). Run `./repo --help` to discover commands.
- **Config**: `config.yaml` at the project root.
- **Framework path**: `{framework_rel}/`

### Contributing to the framework
1. `cd {framework_rel} && git fetch origin && git switch main && git pull --ff-only origin main`
2. Make changes, bump the version in `pyproject.toml`, add a `CHANGELOG.md` entry
3. Commit, push, and wait for CI to pass
4. Back in this project: `cd {framework_rel} && git checkout v<new-version>`
5. Commit the submodule pointer update

### Do not edit

These paths are generated or managed by the framework:

- `{framework_rel}/` — contribute upstream instead
- `{framework_rel}/_managed/` — generated venv, lockfile, pyproject
- `repo`, `repo.cmd`, `repo.ps1` — generated CLI shims
"""
