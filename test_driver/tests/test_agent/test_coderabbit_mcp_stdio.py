"""Tests for the coderabbit_mcp stdio MCP server."""

from __future__ import annotations

import io
import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pathlib import Path

from repo_tools.agent.hooks.coderabbit_mcp_stdio import main

_MOD = "repo_tools.agent.hooks.coderabbit_mcp_stdio"
_CR_MOD = "repo_tools.agent.coderabbit"


# ── Helper ────────────────────────────────────────────────────────────────────


def _call(*requests: dict) -> list[dict]:
    """Run main() with the given requests, return parsed JSON responses."""
    lines = "\n".join(json.dumps(r) for r in requests) + "\n"
    captured = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(lines)),
        patch("sys.stdout", captured),
    ):
        main()
    output = captured.getvalue().strip()
    if not output:
        return []
    return [json.loads(line) for line in output.splitlines() if line.strip()]


# ── Protocol tests ────────────────────────────────────────────────────────────


def test_initialize():
    responses = _call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "coderabbit"


def test_tools_list():
    responses = _call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(responses) == 1
    tools = responses[0]["result"]["tools"]
    names = [t["name"] for t in tools]
    assert "coderabbit_review" in names

    cr = next(t for t in tools if t["name"] == "coderabbit_review")
    schema = cr["inputSchema"]
    assert "worktree_path" in schema["properties"]
    assert "type" in schema["properties"]
    assert schema["properties"]["type"]["enum"] == ["committed", "uncommitted", "all"]


def test_ping():
    responses = _call({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert len(responses) == 1
    assert responses[0]["result"] == {}


def test_notification_no_response():
    """Requests without an 'id' field (notifications) produce no output."""
    responses = _call({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert responses == []


def test_unknown_method():
    responses = _call({"jsonrpc": "2.0", "id": 4, "method": "some/unknown"})
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == -32601


def test_invalid_json_skipped():
    """Invalid JSON lines are silently skipped; subsequent valid requests work."""
    lines = 'not-valid-json\n{"jsonrpc":"2.0","id":5,"method":"ping"}\n'
    captured = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(lines)),
        patch("sys.stdout", captured),
    ):
        main()
    output = captured.getvalue().strip()
    responses = [json.loads(line) for line in output.splitlines() if line.strip()]
    assert len(responses) == 1
    assert responses[0]["result"] == {}


# ── Tool: not installed ───────────────────────────────────────────────────────


def test_not_installed():
    with patch(f"{_CR_MOD}.check_installed", return_value=False):
        responses = _call({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {}},
        })
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "not installed" in text


# ── Tool: not authenticated ───────────────────────────────────────────────────


def test_not_authenticated():
    mock_proc = MagicMock()
    mock_proc.returncode = 1

    with (
        patch(f"{_CR_MOD}.check_installed", return_value=True),
        patch("subprocess.run", return_value=mock_proc),
    ):
        responses = _call({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {}},
        })
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "not authenticated" in text


# ── Tool: auth exception ──────────────────────────────────────────────────────


def test_auth_exception():
    with (
        patch(f"{_CR_MOD}.check_installed", return_value=True),
        patch("subprocess.run", side_effect=OSError("connection refused")),
    ):
        responses = _call({
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {}},
        })
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "fall back" in text


# ── Tool: review success ──────────────────────────────────────────────────────


def test_review_success():
    auth_proc = MagicMock()
    auth_proc.returncode = 0
    review_proc = MagicMock()
    review_proc.stdout = "Found 2 issues:\n- line 10: unused variable\n"
    review_proc.stderr = ""

    with (
        patch(f"{_CR_MOD}.check_installed", return_value=True),
        patch("subprocess.run", side_effect=[auth_proc, review_proc]),
        patch.object(Path, "is_dir", return_value=True),
    ):
        responses = _call({
            "jsonrpc": "2.0", "id": 13, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {"worktree_path": "/some/path"}},
        })
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result.get("isError") is None
    text = result["content"][0]["text"]
    assert "unused variable" in text


# ── Tool: empty output → "No issues found" ───────────────────────────────────


def test_review_empty_output():
    auth_proc = MagicMock()
    auth_proc.returncode = 0
    review_proc = MagicMock()
    review_proc.stdout = ""
    review_proc.stderr = ""

    with (
        patch(f"{_CR_MOD}.check_installed", return_value=True),
        patch("subprocess.run", side_effect=[auth_proc, review_proc]),
    ):
        responses = _call({
            "jsonrpc": "2.0", "id": 14, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {}},
        })
    assert len(responses) == 1
    text = responses[0]["result"]["content"][0]["text"]
    assert "No issues found" in text


# ── Tool: timeout ─────────────────────────────────────────────────────────────


def test_review_timeout():
    auth_proc = MagicMock()
    auth_proc.returncode = 0

    def _side_effect(*args, **kwargs):
        if args and "auth" in args[0]:
            return auth_proc
        raise subprocess.TimeoutExpired(cmd="coderabbit review", timeout=120)

    with (
        patch(f"{_CR_MOD}.check_installed", return_value=True),
        patch("subprocess.run", side_effect=_side_effect),
    ):
        responses = _call({
            "jsonrpc": "2.0", "id": 15, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {}},
        })
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result.get("isError") is True
    assert "timed out" in result["content"][0]["text"]


# ── Tool: review exception ────────────────────────────────────────────────────


def test_review_exception():
    auth_proc = MagicMock()
    auth_proc.returncode = 0

    def _side_effect(*args, **kwargs):
        if args and "auth" in args[0]:
            return auth_proc
        raise OSError("something went wrong")

    with (
        patch(f"{_CR_MOD}.check_installed", return_value=True),
        patch("subprocess.run", side_effect=_side_effect),
    ):
        responses = _call({
            "jsonrpc": "2.0", "id": 16, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {}},
        })
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result.get("isError") is True
    assert "fall back" in result["content"][0]["text"]


# ── Tool: worktree defaults to "." ────────────────────────────────────────────


def test_worktree_defaults_to_dot():
    auth_proc = MagicMock()
    auth_proc.returncode = 0
    review_proc = MagicMock()
    review_proc.stdout = "ok"
    review_proc.stderr = ""

    captured_calls: list = []

    def _side_effect(*args, **kwargs):
        captured_calls.append((args, kwargs))
        if args and "auth" in " ".join(args[0]):
            return auth_proc
        return review_proc

    with (
        patch(f"{_CR_MOD}.check_installed", return_value=True),
        patch("subprocess.run", side_effect=_side_effect),
    ):
        _call({
            "jsonrpc": "2.0", "id": 17, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {}},
        })

    # Second call is the review — check cwd
    review_call_kwargs = captured_calls[1][1]
    assert review_call_kwargs.get("cwd") == "."


# ── Tool: custom type ─────────────────────────────────────────────────────────


def test_custom_type():
    auth_proc = MagicMock()
    auth_proc.returncode = 0
    review_proc = MagicMock()
    review_proc.stdout = "ok"
    review_proc.stderr = ""

    captured_calls: list = []

    def _side_effect(*args, **kwargs):
        captured_calls.append((args, kwargs))
        if args and "auth" in " ".join(args[0]):
            return auth_proc
        return review_proc

    with (
        patch(f"{_CR_MOD}.check_installed", return_value=True),
        patch("subprocess.run", side_effect=_side_effect),
    ):
        _call({
            "jsonrpc": "2.0", "id": 18, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {"type": "uncommitted"}},
        })

    # Second call is the review — check --type flag
    review_cmd_str = " ".join(captured_calls[1][0][0])
    assert "--type" in review_cmd_str
    assert "uncommitted" in review_cmd_str


# ── Windows / WSL tests ───────────────────────────────────────────────────────


def test_windows_wsl_not_installed():
    """On Windows, 'command -v coderabbit' returning non-zero means not installed."""
    wsl_fail = MagicMock()
    wsl_fail.returncode = 1

    with (
        patch(f"{_CR_MOD}.is_windows", return_value=True),
        patch("subprocess.run", return_value=wsl_fail),
    ):
        responses = _call({
            "jsonrpc": "2.0", "id": 50, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {}},
        })
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result.get("isError") is True
    assert "not installed" in result["content"][0]["text"]
    assert "Windows" in result["content"][0]["text"]


def test_windows_review_uses_wsl_prefix():
    """On Windows, all coderabbit subprocess calls go through 'wsl bash -lc'."""
    install_ok = MagicMock(returncode=0)
    auth_proc = MagicMock(returncode=0, stdout="", stderr="")
    review_proc = MagicMock(stdout="looks good", stderr="")

    captured_cmds: list = []

    def _side_effect(*args, **kwargs):
        captured_cmds.append(args[0])
        if "command -v" in " ".join(args[0]):
            return install_ok
        if "auth" in " ".join(args[0]):
            return auth_proc
        return review_proc

    with (
        patch(f"{_CR_MOD}.is_windows", return_value=True),
        patch("subprocess.run", side_effect=_side_effect),
    ):
        _call({
            "jsonrpc": "2.0", "id": 51, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {}},
        })

    # install check, auth, review — all through wsl bash -lc
    assert len(captured_cmds) == 3
    for cmd in captured_cmds:
        assert cmd[0] == "wsl", f"Expected 'wsl' prefix, got: {cmd}"
        assert cmd[1] == "bash", f"Expected 'bash' login shell, got: {cmd}"


# ── Exception handling in main loop (Issue 5) ─────────────────────────────────


def test_dispatch_exception_with_id_returns_error():
    """When _dispatch raises and the request has an id, return JSON-RPC error."""
    captured = io.StringIO()
    captured_stderr = io.StringIO()
    req = {"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {"name": "coderabbit_review", "arguments": {}}}
    lines = json.dumps(req) + "\n"

    with (
        patch("sys.stdin", io.StringIO(lines)),
        patch("sys.stdout", captured),
        patch("sys.stderr", captured_stderr),
        patch(f"{_MOD}._dispatch", side_effect=RuntimeError("boom")),
    ):
        main()

    output = captured.getvalue().strip()
    responses = [json.loads(line) for line in output.splitlines() if line.strip()]
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == -32603
    assert responses[0]["error"]["message"] == "Internal error"
    assert "boom" in captured_stderr.getvalue()


def test_dispatch_exception_on_notification_no_response():
    """When _dispatch raises for a notification (no id), no output is produced."""
    captured = io.StringIO()
    captured_stderr = io.StringIO()
    req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    lines = json.dumps(req) + "\n"

    with (
        patch("sys.stdin", io.StringIO(lines)),
        patch("sys.stdout", captured),
        patch("sys.stderr", captured_stderr),
        patch(f"{_MOD}._dispatch", side_effect=RuntimeError("silent")),
    ):
        main()

    output = captured.getvalue().strip()
    assert output == ""
    assert "silent" in captured_stderr.getvalue()


# ── worktree_path validation (Issue 6) ────────────────────────────────────────


def test_nonexistent_worktree_path_returns_error():
    """A nonexistent worktree_path should return an error without running coderabbit."""
    responses = _call({
        "jsonrpc": "2.0", "id": 60, "method": "tools/call",
        "params": {"name": "coderabbit_review", "arguments": {"worktree_path": "/nonexistent/path/12345"}},
    })
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result.get("isError") is True
    assert "not a directory" in result["content"][0]["text"]
