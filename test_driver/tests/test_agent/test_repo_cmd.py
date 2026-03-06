"""Tests for dynamic repo command MCP tools."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_tools.agent.repo_cmd import (
    _discover_repo_commands,
    build_tool_handlers,
    build_tool_schemas,
    call_repo_run,
)


# ── Discovery ────────────────────────────────────────────────────────────────


def test_discover_sections_with_steps():
    config = {
        "test": {"steps": ['echo "hello"']},
        "build": {"steps": ["make all"]},
        "repo": {"tokens": {}},
        "agent": {"backend": "sdk"},
    }
    commands = _discover_repo_commands(config)
    names = [c["name"] for c in commands]
    assert "test" in names
    assert "build" in names
    assert "repo" not in names
    assert "agent" not in names


def test_discover_platform_filter_steps():
    config = {
        "build": {"steps@linux": ["make"], "steps@windows": ["nmake"]},
    }
    commands = _discover_repo_commands(config)
    assert len(commands) == 1
    assert commands[0]["name"] == "build"


def test_discover_skips_clean():
    config = {
        "clean": {"steps": ["rm -rf build"]},
        "test": {"steps": ["pytest"]},
    }
    commands = _discover_repo_commands(config)
    names = [c["name"] for c in commands]
    assert "clean" not in names
    assert "test" in names


def test_discover_skips_non_dict_sections():
    config = {
        "test": {"steps": ["pytest"]},
        "some_list": [1, 2, 3],
        "some_string": "hello",
    }
    commands = _discover_repo_commands(config)
    assert len(commands) == 1
    assert commands[0]["name"] == "test"


def test_discover_skips_sections_without_steps():
    config = {
        "myconfig": {"setting": "value"},
        "test": {"steps": ["pytest"]},
    }
    commands = _discover_repo_commands(config)
    assert len(commands) == 1


def test_discover_empty_config():
    assert _discover_repo_commands({}) == []


# ── Execution ────────────────────────────────────────────────────────────────


def test_call_repo_run_success(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = "All tests passed.\n"
    mock_proc.stderr = ""
    mock_proc.returncode = 0

    with patch("subprocess.run", return_value=mock_proc) as mock_run:
        result = call_repo_run("test", {}, workspace_root=tmp_path)

    assert result["text"] == "All tests passed."
    assert "isError" not in result
    cmd = mock_run.call_args[0][0]
    assert "repo_tools.cli" in " ".join(cmd)
    assert "test" in cmd


def test_call_repo_run_with_extra_args(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = "ok"
    mock_proc.stderr = ""
    mock_proc.returncode = 0

    with patch("subprocess.run", return_value=mock_proc) as mock_run:
        call_repo_run("test", {"extra_args": "--verbose -x"}, workspace_root=tmp_path)

    cmd = mock_run.call_args[0][0]
    assert "--verbose" in cmd
    assert "-x" in cmd


def test_call_repo_run_failure(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = ""
    mock_proc.stderr = "Error: build failed\n"
    mock_proc.returncode = 1

    with patch("subprocess.run", return_value=mock_proc):
        result = call_repo_run("build", {}, workspace_root=tmp_path)

    assert result["isError"] is True
    assert "build failed" in result["text"]


def test_call_repo_run_timeout(tmp_path):
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="test", timeout=300),
    ):
        result = call_repo_run("test", {}, workspace_root=tmp_path)

    assert result["isError"] is True
    assert "timed out" in result["text"]


def test_call_repo_run_empty_output_success(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = ""
    mock_proc.stderr = ""
    mock_proc.returncode = 0

    with patch("subprocess.run", return_value=mock_proc):
        result = call_repo_run("format", {}, workspace_root=tmp_path)

    assert result["text"] == "repo format completed."


def test_call_repo_run_empty_output_failure(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = ""
    mock_proc.stderr = ""
    mock_proc.returncode = 2

    with patch("subprocess.run", return_value=mock_proc):
        result = call_repo_run("build", {}, workspace_root=tmp_path)

    assert result["isError"] is True
    assert "exit code 2" in result["text"]


# ── Schema/handler builders ──────────────────────────────────────────────────


def test_build_tool_schemas():
    config = {"test": {"steps": ["pytest"]}, "build": {"steps": ["make"]}}
    schemas = build_tool_schemas(config)
    names = [s["name"] for s in schemas]
    assert "repo_test" in names
    assert "repo_build" in names
    for s in schemas:
        assert "inputSchema" in s
        assert "extra_args" in s["inputSchema"]["properties"]


def test_build_tool_handlers(tmp_path):
    config = {"test": {"steps": ["pytest"]}}
    handlers = build_tool_handlers(config, tmp_path)
    assert "repo_test" in handlers

    mock_proc = MagicMock()
    mock_proc.stdout = "ok"
    mock_proc.stderr = ""
    mock_proc.returncode = 0

    with patch("subprocess.run", return_value=mock_proc):
        result = handlers["repo_test"]({"extra_args": ""})

    assert result["text"] == "ok"


# ── MCP stdio server ────────────────────────────────────────────────────────


def _call_mcp(*requests, config=None, project_root=None):
    """Run the repo_cmd MCP server with the given requests."""
    from repo_tools.agent.mcp.repo_cmd import main

    if config is None:
        config = {"test": {"steps": ["pytest"]}}
    if project_root is None:
        project_root = "/tmp/test"

    lines = "\n".join(json.dumps(r) for r in requests) + "\n"
    captured = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(lines)),
        patch("sys.stdout", captured),
        patch("sys.argv", [
            "repo_cmd_mcp",
            "--project-root", str(project_root),
            "--config", json.dumps(config),
        ]),
    ):
        main()
    output = captured.getvalue().strip()
    if not output:
        return []
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_mcp_initialize():
    responses = _call_mcp(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert len(responses) == 1
    assert responses[0]["result"]["serverInfo"]["name"] == "repo_cmd"


def test_mcp_tools_list():
    config = {"test": {"steps": ["pytest"]}, "build": {"steps": ["make"]}}
    responses = _call_mcp(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        config=config,
    )
    tools = responses[0]["result"]["tools"]
    names = [t["name"] for t in tools]
    assert "repo_test" in names
    assert "repo_build" in names


def test_mcp_tool_call(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = "3 passed"
    mock_proc.stderr = ""
    mock_proc.returncode = 0

    with patch("subprocess.run", return_value=mock_proc):
        responses = _call_mcp(
            {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "repo_test", "arguments": {}},
            },
            project_root=str(tmp_path),
        )

    result = responses[0]["result"]
    assert "3 passed" in result["content"][0]["text"]


def test_mcp_unknown_tool():
    responses = _call_mcp({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "nonexistent", "arguments": {}},
    })
    result = responses[0]["result"]
    assert result.get("isError") is True
