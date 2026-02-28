"""Patch a .gitignore file with repokit-managed entries."""

from __future__ import annotations

import sys
from pathlib import Path

ENTRIES = ["_tools/", "tools/pyproject.toml", "tools/uv.lock", "repo", "repo.cmd", "_agent/", "config.local.yaml"]
MARKER = "# repokit"


def patch_gitignore(
    path: Path,
    entries: list[str] = ENTRIES,
    marker: str = MARKER,
) -> None:
    """Ensure *entries* exist in the gitignore file under *marker*.

    Creates the file if absent.  Preserves original line endings (CRLF/LF).
    Idempotent — does nothing when all entries are already present.
    """
    raw = b""
    if path.exists():
        raw = path.read_bytes()

    # Detect line ending style from existing content.
    eol = "\r\n" if b"\r\n" in raw else "\n"

    text = raw.decode()
    existing_lines = {l.rstrip("\r\n") for l in text.splitlines()}

    missing = [e for e in entries if e not in existing_lines]
    if not missing:
        return

    parts: list[str] = []

    # Ensure trailing newline on existing content.
    if text and not text.endswith("\n"):
        parts.append(eol)

    # Blank separator + marker (only when file already has content and
    # the marker isn't present yet).
    if text and marker not in existing_lines:
        parts.append(eol)
        parts.append(marker + eol)
    elif not text:
        parts.append(marker + eol)

    for entry in missing:
        parts.append(entry + eol)

    path.write_bytes(text.encode() + "".join(parts).encode())


if __name__ == "__main__":
    patch_gitignore(Path(sys.argv[1]))
