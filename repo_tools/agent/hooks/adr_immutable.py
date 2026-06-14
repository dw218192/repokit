"""Claude Code PreToolUse hook denying edits to an *accepted* ADR.

Reads a PreToolUse event from stdin (matcher: ``Write``/``Edit``) and denies the
call when the target file is an ADR whose on-disk frontmatter is
``status: accepted``. Everything else is allowed — proposed/superseded ADRs stay
editable, and non-ADR paths are out of scope for this hook.

This realizes the immutability half of the workflow integrity model (ADR-2,
`workflow.md`): an accepted decision is amended only by a *superseding* ADR,
never by editing the accepted file in place. It is a portable, dependency-free
hook — deliberately not part of the (dying) ``check_bash`` allowlist machinery.

Matching is by path: a file is an ADR when it lives under a ``docs/adr/``
segment. The ``status`` is read from the existing file on disk (the pre-edit
state), so an agent cannot flip ``accepted`` → ``proposed`` and edit in the same
breath — the deny is evaluated against what is already committed.

Exit codes:
    0 — decision written to stdout (allow or deny)
    2 — error (stderr is fed back to Claude as an error message)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import write_log


def _is_adr_path(path: Path) -> bool:
    """True when *path* sits under a ``docs/adr/`` directory and is markdown."""
    if path.suffix.lower() != ".md":
        return False
    parts = [p.lower() for p in path.parts]
    # look for an "adr" dir immediately under a "docs" dir
    for i in range(len(parts) - 1):
        if parts[i] == "docs" and parts[i + 1] == "adr":
            return True
    return False


def _frontmatter_status(text: str) -> str | None:
    """Extract the ``status:`` value from a leading ``---`` frontmatter block.

    Returns the lowercased status string, or ``None`` when there is no
    frontmatter / no status key. Tolerates the plain ``Status: accepted`` line
    style used by the existing hand-authored ADRs (no ``---`` fence), so the
    hook protects the foundational set authored under the inception clause too.
    """
    lines = text.splitlines()

    # Fenced YAML frontmatter: --- ... ---
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            key, sep, value = line.partition(":")
            if sep and key.strip().lower() == "status":
                return value.strip().strip("\"'").lower() or None
        return None

    # Fallback: a leading "Status: <x>" line within the first few lines
    # (matches docs/adr/*.md, which use a bare status line, not a fence).
    for line in lines[:5]:
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "status":
            # value may carry a trailing parenthetical, e.g. "accepted (…)".
            token = value.strip().strip("\"'").split()
            return token[0].lower() if token else None
    return None


def _evaluate(file_path_str: str) -> tuple[bool, str]:
    """Return (allowed, reason) for an edit targeting *file_path_str*."""
    if not file_path_str:
        return True, ""
    path = Path(file_path_str)
    if not _is_adr_path(path):
        return True, ""
    if not path.exists():
        # Creating a new ADR is allowed; immutability binds only once accepted.
        return True, ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        # Can't read it → don't block on a transient IO error.
        return True, f"(adr_immutable: unreadable, allowed: {exc})"
    if _frontmatter_status(text) == "accepted":
        return False, (
            f"ADR IMMUTABILITY: '{path.as_posix()}' has status 'accepted' and "
            f"cannot be edited. An accepted ADR is immutable; amend it by "
            f"authoring a NEW superseding ADR (set this one's status to "
            f"'superseded' only via that supersession), never by editing the "
            f"accepted file in place. Do NOT retry this edit."
        )
    return True, ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deny Write/Edit on an accepted ADR (PreToolUse hook).",
    )
    parser.add_argument("--debug-log", default=None, help="Append decisions to this file")
    args = parser.parse_args()

    log_path = Path(args.debug_log) if args.debug_log else None

    try:
        event = json.load(sys.stdin)
        tool_name = event.get("tool_name", "")
        if tool_name not in ("Write", "Edit"):
            print(
                f"adr_immutable: unsupported tool_name {tool_name!r} — "
                "expected Write or Edit",
                file=sys.stderr,
            )
            sys.exit(2)
        file_path = event.get("tool_input", {}).get("file_path", "")
        allowed, reason = _evaluate(file_path)
    except Exception as exc:
        print(f"adr_immutable error: {exc}", file=sys.stderr)
        sys.exit(2)

    if allowed:
        if log_path:
            write_log(log_path, file_path, "allow", reason)
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }
    else:
        if log_path:
            write_log(log_path, file_path, "deny", reason)
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    json.dump(decision, sys.stdout)


if __name__ == "__main__":
    main()
