"""CommandRunnerTool — runs configured steps (command list) with token expansion."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

import click

from .core import CommandGroup, RepoTool, ShellCommand, TokenFormatter, ToolContext, logger

_STEP_KEYS = {"command", "cwd", "env_script", "env"}


def _validate_steps(section_name: str, raw: Any) -> list[dict]:
    """Validate and normalize a ``steps`` value into a list of step dicts.

    Each item is either a string (shorthand for ``{"command": str}``) or a dict
    with ``command`` (required), plus optional ``cwd``, ``env_script``, ``env``.

    Raises ``SystemExit(1)`` on validation failure.
    """
    if not isinstance(raw, list):
        print(f"Error: '{section_name}' steps must be a list, got {type(raw).__name__}", file=sys.stderr)
        raise SystemExit(1)

    result: list[dict] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            result.append({"command": item})
        elif isinstance(item, dict):
            if "command" not in item:
                print(
                    f"Error: '{section_name}' step [{i}] missing required 'command' key",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            unknown = set(item) - _STEP_KEYS
            if unknown:
                print(
                    f"Error: '{section_name}' step [{i}] has unknown keys: {sorted(unknown)}",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            env_val = item.get("env")
            if env_val is not None and (
                not isinstance(env_val, list) or not all(isinstance(e, str) for e in env_val)
            ):
                print(
                    f"Error: '{section_name}' step [{i}] 'env' must be a list of strings",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            result.append(dict(item))
        else:
            print(
                f"Error: '{section_name}' step [{i}] must be a string or dict, got {type(item).__name__}",
                file=sys.stderr,
            )
            raise SystemExit(1)
    return result


def _parse_env_list(entries: list[str]) -> dict[str, str]:
    """Convert ``["KEY=VALUE", ...]`` to a dict.

    Raises ``SystemExit(1)`` if any entry is missing ``=``.
    """
    result: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            print(f"Error: env entry {entry!r} missing '='", file=sys.stderr)
            raise SystemExit(1)
        key, value = entry.split("=", 1)
        result[key] = value
    return result


class CommandRunnerTool(RepoTool):
    """Tool that runs configured steps with token expansion.

    Supports ``--dry-run`` to print the resolved commands without executing.
    """

    config_hint: str = ""

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option("--dry-run", is_flag=True, help="Print resolved command without executing")(cmd)
        return cmd

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        raw_steps = args.get("steps")
        if not raw_steps:
            logger.error(
                f"No {self.name!r} steps configured. Add to config.yaml:\n"
                f"  {self.config_hint}"
            )
            raise SystemExit(1)

        tokens = dict(ctx.tokens)

        # Merge remaining args as extra tokens (skip step-framework keys).
        _skip = {"steps", "dry_run"}
        for k, v in args.items():
            if k not in _skip and v is not None:
                tokens[k] = str(v)

        formatter = TokenFormatter(tokens)
        steps = _validate_steps(self.name, raw_steps)

        # Resolve each step through token expansion.
        resolved: list[dict] = []
        for step in steps:
            r: dict[str, Any] = {}
            r["command"] = formatter.resolve(step["command"])
            if "cwd" in step:
                r["cwd"] = Path(formatter.resolve(str(step["cwd"])))
            if "env_script" in step:
                r["env_script"] = Path(formatter.resolve(str(step["env_script"])))
            if "env" in step:
                expanded = [formatter.resolve(e) for e in step["env"]]
                r["env"] = _parse_env_list(expanded)
            resolved.append(r)

        n = len(resolved)

        if args.get("dry_run"):
            for i, step in enumerate(resolved, 1):
                logger.info(f"Would run [{i}/{n}]: {step['command']}")
            return

        if n == 1:
            step = resolved[0]
            logger.info(f"Running: {step['command']}")
            ShellCommand(
                shlex.split(step["command"]),
                env_script=step.get("env_script"),
                cwd=step.get("cwd"),
                env=step.get("env"),
            ).exec()
        else:
            with CommandGroup(self.name) as group:
                for step in resolved:
                    group.run(
                        shlex.split(step["command"]),
                        env_script=step.get("env_script"),
                        cwd=step.get("cwd"),
                        env=step.get("env"),
                    )
