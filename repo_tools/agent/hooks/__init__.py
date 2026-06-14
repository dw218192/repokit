"""Unified hook entrypoint for Claude Code hooks.

Usage::

    python -m repo_tools.agent.hooks adr_immutable [--debug-log <path>]
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


def write_log(log_path: Path, command: str, decision: str, reason: str = "") -> None:
    """Append one line to the hook debug log."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {decision:5s}  {command!r}"
        if reason:
            line += f"  # {reason}"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        print(f"warning: hook log write failed: {exc}", file=sys.stderr)


def main() -> None:
    """Dispatch to the correct hook subcommand."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python -m repo_tools.agent.hooks adr_immutable [args...]", file=sys.stderr)
        sys.exit(2)

    subcommand = sys.argv[1]
    # Remove the subcommand from argv so the sub-module's argparse sees the right args
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if subcommand == "adr_immutable":
        from .adr_immutable import main as sub_main
    else:
        print(f"Unknown subcommand: {subcommand!r}", file=sys.stderr)
        sys.exit(2)

    sub_main()
