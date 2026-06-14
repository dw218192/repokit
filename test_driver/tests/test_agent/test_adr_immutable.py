"""Tests for the ADR-immutability PreToolUse hook."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run(file_path: str, tool_name: str = "Edit", via_unified: bool = False) -> dict:
    """Drive the hook with a synthetic Write/Edit event; return its decision."""
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
        "cwd": str(Path(file_path).parent),
    }
    if via_unified:
        cmd = [sys.executable, "-m", "repo_tools.agent.hooks", "adr_immutable"]
    else:
        cmd = [sys.executable, "-m", "repo_tools.agent.hooks.adr_immutable"]
    result = subprocess.run(cmd, input=json.dumps(event), capture_output=True, text=True)
    if result.returncode == 2:
        pytest.fail(f"Hook error: {result.stderr}")
    assert result.returncode == 0, f"Hook exited {result.returncode}: {result.stderr}"
    return json.loads(result.stdout)


def _decision(out: dict) -> str:
    return out["hookSpecificOutput"]["permissionDecision"]


def _adr(tmp_path: Path, status_block: str) -> Path:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True, exist_ok=True)
    p = adr_dir / "1-some-decision.md"
    p.write_text(status_block + "\n\n# ADR-1\n\nbody\n", encoding="utf-8")
    return p


# ── accepted ADRs are denied ─────────────────────────────────────────


def test_accepted_bare_status_denied(tmp_path: Path):
    """The hand-authored `Status: accepted` (no fence) style is protected."""
    adr = _adr(tmp_path, "# ADR-1 — title\n\nStatus: accepted\nDate: 2026-06")
    out = _run(str(adr))
    assert _decision(out) == "deny"
    assert "immutab" in out["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_accepted_fenced_frontmatter_denied(tmp_path: Path):
    adr = _adr(tmp_path, "---\nid: ADR-1\nstatus: accepted\n---")
    assert _decision(_run(str(adr))) == "deny"


def test_accepted_denied_for_write_too(tmp_path: Path):
    adr = _adr(tmp_path, "---\nstatus: accepted\n---")
    assert _decision(_run(str(adr), tool_name="Write")) == "deny"


def test_accepted_denied_via_unified_entrypoint(tmp_path: Path):
    adr = _adr(tmp_path, "---\nstatus: accepted\n---")
    assert _decision(_run(str(adr), via_unified=True)) == "deny"


# ── non-accepted ADRs stay editable ──────────────────────────────────


@pytest.mark.parametrize("status", ["proposed", "superseded"])
def test_non_accepted_allowed(tmp_path: Path, status: str):
    adr = _adr(tmp_path, f"---\nstatus: {status}\n---")
    assert _decision(_run(str(adr))) == "allow"


def test_accepted_substring_status_not_matched(tmp_path: Path):
    """`status: accepted-by-mistake` must not be read as accepted (token match)."""
    adr = _adr(tmp_path, "Status: proposed")
    assert _decision(_run(str(adr))) == "allow"


# ── scope: only existing ADR paths bind ──────────────────────────────


def test_new_adr_path_allowed(tmp_path: Path):
    """Creating a not-yet-existing ADR is allowed; immutability binds once accepted."""
    path = tmp_path / "docs" / "adr" / "9-new.md"
    assert _decision(_run(str(path), tool_name="Write")) == "allow"


def test_non_adr_path_allowed(tmp_path: Path):
    """A normal source/spec file is out of scope even with status: accepted."""
    spec_dir = tmp_path / "docs" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    p = spec_dir / "x.md"
    p.write_text("---\nstatus: accepted\n---\nbody\n", encoding="utf-8")
    assert _decision(_run(str(p))) == "allow"


def test_non_markdown_adr_dir_file_allowed(tmp_path: Path):
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True, exist_ok=True)
    p = adr_dir / "notes.txt"
    p.write_text("Status: accepted\n", encoding="utf-8")
    assert _decision(_run(str(p))) == "allow"
