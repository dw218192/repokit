"""Claude Code PreToolUse hook for Bash command permission checking.

Reads a PreToolUse event from stdin, checks the Bash command against
the rules file, and outputs a hookSpecificOutput JSON decision.

When invoked with matcher "Write" or "Edit", checks that the target
``file_path`` is under ``--project-root`` (or a system temp dir) and
blocks writes outside that boundary.

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
import tempfile
from pathlib import Path

from ..rules import check_command, load_rules
from . import write_log


def _is_under(child: Path, parent: Path) -> bool:
    """Return True if *child* is equal to or under *parent* (resolved)."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _check_file_path(file_path_str: str, project_root: Path, tool_name_hint: str = "Write/Edit") -> tuple[bool, str]:
    """Check that *file_path_str* is under *project_root* or a temp dir."""
    target = Path(file_path_str).resolve()
    if _is_under(target, project_root):
        return True, ""
    if _is_under(target, Path(tempfile.gettempdir())):
        return True, ""
    tmp_dir = Path(tempfile.gettempdir()).resolve()
    return False, (
        f"WORKTREE ISOLATION: {tool_name_hint} to '{target}' is BLOCKED because "
        f"the path is outside your worktree root '{project_root.resolve()}'. "
        f"You are running inside an isolated worktree -- all file writes MUST "
        f"target paths under '{project_root.resolve()}' (or the system temp "
        f"directory '{tmp_dir}'). "
        f"Do NOT retry this path. Instead, rewrite the path to be under your "
        f"worktree root. If you intended to write to the main repo, that is not "
        f"allowed from a worktree worker -- your changes must stay in the worktree."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Bash commands against agent rules.")
    parser.add_argument("--rules", required=False, default=None, help="Path to rules.toml")
    parser.add_argument("--extra-rules", action="append", default=[], help="Extra rules files to merge (may be repeated)")
    parser.add_argument("--project-root", default=None, help="Project root for dir constraints")
    parser.add_argument("--debug-log", default=None, help="Append hook decisions to this file")
    parser.add_argument("--role", default=None, help="Agent role for role-specific rule filtering")
    args = parser.parse_args()

    log_path = Path(args.debug_log) if args.debug_log else None

    # Read PreToolUse event from stdin and evaluate rules.
    # Any exception here exits with code 2 so Claude sees a clear error message
    # rather than an unhandled traceback (which would also exit non-zero but with
    # a less informative exit code).
    try:
        event = json.load(sys.stdin)
        tool_name = event.get("tool_name", "")
        project_root = Path(args.project_root) if args.project_root else Path(event.get("cwd", "."))

        # Write/Edit file-path confinement check
        if tool_name in ("Write", "Edit"):
            file_path = event.get("tool_input", {}).get("file_path", "")
            allowed, reason = _check_file_path(file_path, project_root, tool_name_hint=tool_name)
        else:
            # Bash command check — requires rules file
            rules_path = Path(args.rules) if args.rules else None
            if rules_path is None or not rules_path.exists():
                print(f"Rules file not found: {rules_path}", file=sys.stderr)
                sys.exit(2)
            command = event.get("tool_input", {}).get("command", "")
            cwd = Path(event.get("cwd", "."))
            extra = [Path(p) for p in args.extra_rules]
            rules = load_rules(rules_path, role=args.role, extra_paths=extra)
            allowed, reason = check_command(command, rules, project_root=project_root, cwd=cwd)
    except Exception as exc:
        print(f"check_bash error: {exc}", file=sys.stderr)
        sys.exit(2)

    log_label = event.get("tool_input", {}).get("file_path", "") if tool_name in ("Write", "Edit") else event.get("tool_input", {}).get("command", "")

    if allowed:
        if log_path:
            write_log(log_path, log_label, "allow")
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }
    else:
        if log_path:
            write_log(log_path, log_label, "deny", reason)

        deny_reason = f"Blocked: {reason}."
        if tool_name not in ("Write", "Edit") and args.rules:
            rules_path = Path(args.rules)
            try:
                rel_rules = rules_path.resolve().relative_to(project_root.resolve()).as_posix()
            except ValueError:
                rel_rules = rules_path.as_posix()
            deny_reason += f" Rules: {rel_rules}"

        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            }
        }

    json.dump(decision, sys.stdout)


if __name__ == "__main__":
    main()
