"""Claude Code PreToolUse hook for Bash command permission checking.

Reads a PreToolUse event from stdin, checks the Bash command against
the rules file, and outputs a hookSpecificOutput JSON decision.

Usage (in Claude Code settings)::

    {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "./repo python -m repo_tools.agent.hooks.check_bash --rules <path>"}
    ]}]}}

Exit codes:
    0 — decision written to stdout (allow or deny)
    2 — error (stderr is fed back to Claude as an error message)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from ..rules import check_command, load_rules


def _write_log(log_path: Path, command: str, decision: str, reason: str = "") -> None:
    """Append one line to the hook debug log."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {decision:5s}  {command!r}"
        if reason:
            line += f"  # {reason}"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # Never let logging break the hook


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Bash commands against agent rules.")
    parser.add_argument("--rules", required=True, help="Path to rules.toml")
    parser.add_argument("--project-root", default=None, help="Project root for dir constraints")
    parser.add_argument("--debug-log", default=None, help="Append hook decisions to this file")
    parser.add_argument("--role", default=None, help="Agent role for role-specific rule filtering")
    args = parser.parse_args()

    log_path = Path(args.debug_log) if args.debug_log else None

    rules_path = Path(args.rules)
    if not rules_path.exists():
        print(f"Rules file not found: {rules_path}", file=sys.stderr)
        sys.exit(2)

    # Read PreToolUse event from stdin and evaluate rules.
    # Any exception here exits with code 2 so Claude sees a clear error message
    # rather than an unhandled traceback (which would also exit non-zero but with
    # a less informative exit code).
    try:
        event = json.load(sys.stdin)
        command = event.get("tool_input", {}).get("command", "")

        project_root = Path(args.project_root) if args.project_root else Path(event.get("cwd", "."))
        cwd = Path(event.get("cwd", "."))

        rules = load_rules(rules_path, role=args.role)
        allowed, reason = check_command(command, rules, project_root=project_root, cwd=cwd)
    except Exception as exc:
        print(f"check_bash error: {exc}", file=sys.stderr)
        sys.exit(2)

    if allowed:
        if log_path:
            _write_log(log_path, command, "allow")
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }
    else:
        if log_path:
            _write_log(log_path, command, "deny", reason)

        # Show path relative to project root so the agent can find the file
        try:
            rel_rules = rules_path.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            rel_rules = rules_path.as_posix()

        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Blocked: {reason}. "
                    f"Rules: {rel_rules}"
                ),
            }
        }

    json.dump(decision, sys.stdout)


if __name__ == "__main__":
    main()
