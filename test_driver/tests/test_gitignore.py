"""Tests for repo_tools.gitignore.patch_gitignore()."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_tools.gitignore import ENTRIES, MARKER, patch_gitignore


class TestPatchGitignore:
    """Unit tests for patch_gitignore()."""

    def test_no_file(self, tmp_path: Path):
        """Creates file with marker + all entries when file is absent."""
        gi = tmp_path / ".gitignore"
        patch_gitignore(gi)

        expected = MARKER + "\n" + "".join(e + "\n" for e in ENTRIES)
        assert gi.read_text() == expected

    def test_empty_file(self, tmp_path: Path):
        """Empty file gets marker + all entries (same as absent)."""
        gi = tmp_path / ".gitignore"
        gi.write_bytes(b"")
        patch_gitignore(gi)

        expected = MARKER + "\n" + "".join(e + "\n" for e in ENTRIES)
        assert gi.read_text() == expected

    def test_existing_content_trailing_newline(self, tmp_path: Path):
        """Appends blank line + marker + entries after existing content."""
        gi = tmp_path / ".gitignore"
        gi.write_text("node_modules\n")
        patch_gitignore(gi)

        expected = (
            "node_modules\n"
            "\n"
            f"{MARKER}\n"
            + "".join(e + "\n" for e in ENTRIES)
        )
        assert gi.read_text() == expected

    def test_existing_content_no_trailing_newline(self, tmp_path: Path):
        """Adds newline before blank line + marker when file lacks trailing newline."""
        gi = tmp_path / ".gitignore"
        gi.write_text("node_modules")
        patch_gitignore(gi)

        expected = (
            "node_modules\n"
            "\n"
            f"{MARKER}\n"
            + "".join(e + "\n" for e in ENTRIES)
        )
        assert gi.read_text() == expected

    def test_some_entries_present(self, tmp_path: Path):
        """Only adds missing entries, not duplicates."""
        gi = tmp_path / ".gitignore"
        gi.write_text("_tools/\n")
        patch_gitignore(gi)

        content = gi.read_text()
        assert content.count("_tools/") == 1
        for entry in ENTRIES:
            assert entry in content.splitlines()

    def test_fully_present(self, tmp_path: Path):
        """No changes when all entries + marker are already present."""
        full = MARKER + "\n" + "".join(e + "\n" for e in ENTRIES)
        gi = tmp_path / ".gitignore"
        gi.write_text(full)

        patch_gitignore(gi)
        assert gi.read_text() == full

    def test_crlf_content(self, tmp_path: Path):
        """Preserves CRLF line endings when appending."""
        gi = tmp_path / ".gitignore"
        gi.write_bytes(b"node_modules\r\n")
        patch_gitignore(gi)

        raw = gi.read_bytes()
        # Original line preserved.
        assert raw.startswith(b"node_modules\r\n")
        # All appended lines use CRLF.
        assert b"\n\r\n" + MARKER.encode() + b"\r\n" in raw
        for entry in ENTRIES:
            assert entry.encode() + b"\r\n" in raw
