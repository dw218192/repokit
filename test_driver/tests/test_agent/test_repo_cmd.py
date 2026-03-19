"""Tests for dynamic repo command MCP tools."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_tools.agent.repo_cmd import (
    _discover_registered_tools,
    _discover_repo_commands,
    _merge_commands,
    build_repo_run_handler,
    build_repo_run_schema,
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


def test_build_repo_run_schema():
    config = {"test": {"steps": ["pytest"]}, "build": {"steps": ["make"]}}
    schema = build_repo_run_schema(config)
    assert schema["name"] == "repo_run"
    enum = schema["inputSchema"]["properties"]["command"]["enum"]
    assert "test" in enum
    assert "build" in enum
    assert "extra_args" in schema["inputSchema"]["properties"]
    assert "command" in schema["inputSchema"]["required"]


def test_build_repo_run_handler(tmp_path):
    config = {"test": {"steps": ["pytest"]}}
    name, handler = build_repo_run_handler(config, tmp_path)
    assert name == "repo_run"

    mock_proc = MagicMock()
    mock_proc.stdout = "ok"
    mock_proc.stderr = ""
    mock_proc.returncode = 0

    with patch("subprocess.run", return_value=mock_proc):
        result = handler({"command": "test", "extra_args": ""})

    assert result["text"] == "ok"


def test_build_repo_run_handler_unknown_command(tmp_path):
    config = {"test": {"steps": ["pytest"]}}
    _name, handler = build_repo_run_handler(config, tmp_path)

    result = handler({"command": "nonexistent"})
    assert result["isError"] is True
    assert "Unknown command" in result["text"]


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
    assert len(tools) == 1
    assert tools[0]["name"] == "repo_run"
    enum = tools[0]["inputSchema"]["properties"]["command"]["enum"]
    assert "test" in enum
    assert "build" in enum


def test_mcp_tool_call(tmp_path):
    mock_proc = MagicMock()
    mock_proc.stdout = "3 passed"
    mock_proc.stderr = ""
    mock_proc.returncode = 0

    with patch("subprocess.run", return_value=mock_proc):
        responses = _call_mcp(
            {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "repo_run", "arguments": {"command": "test"}},
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


# ── Registered tool discovery ─────────────────────────────────────────────────


def test_discover_registered_tools():
    """Registered RepoTool subclasses are discovered (except agent)."""
    from repo_tools.core import RepoTool, _TOOL_REGISTRY

    class _FakeClean(RepoTool):
        name = "clean"
        help = "Clean up"
        def execute(self, ctx, args): pass

    class _FakeAgent(RepoTool):
        name = "agent"
        help = "Agent"
        def execute(self, ctx, args): pass

    saved = dict(_TOOL_REGISTRY)
    try:
        _TOOL_REGISTRY["clean"] = _FakeClean()
        _TOOL_REGISTRY["agent"] = _FakeAgent()
        tools = _discover_registered_tools()
        names = {t["name"] for t in tools}
        assert "clean" in names
        assert "agent" not in names
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert t["description"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


# ── _merge_commands ───────────────────────────────────────────────────────────


def test_merge_commands_deduplicates():
    config_cmds = [{"name": "test", "description": "Run ./repo test"}]
    extra = [
        {"name": "test", "description": "duplicate"},
        {"name": "clean", "description": "Clean up"},
    ]
    merged = _merge_commands(config_cmds, extra)
    names = [c["name"] for c in merged]
    assert names.count("test") == 1
    assert "clean" in names


def test_merge_commands_no_extra():
    cmds = [{"name": "test", "description": "d"}]
    assert _merge_commands(cmds, None) is cmds
    assert _merge_commands(cmds, []) is cmds


# ── Schema/handler with extra tools ───────────────────────────────────────────


def test_build_repo_run_schema_with_extra():
    config = {"test": {"steps": ["pytest"]}}
    extra = [{"name": "clean", "description": "Clean up"}]
    schema = build_repo_run_schema(config, extra=extra)
    enum = schema["inputSchema"]["properties"]["command"]["enum"]
    assert "test" in enum
    assert "clean" in enum


def test_build_repo_run_handler_with_extra(tmp_path):
    config = {"test": {"steps": ["pytest"]}}
    extra = [{"name": "clean", "description": "Clean up"}]
    _name, handler = build_repo_run_handler(config, tmp_path, extra=extra)

    mock_proc = MagicMock()
    mock_proc.stdout = "cleaned"
    mock_proc.stderr = ""
    mock_proc.returncode = 0

    with patch("subprocess.run", return_value=mock_proc):
        result = handler({"command": "clean"})
    assert result["text"] == "cleaned"


# ── MCP stdio server with --extra-tools ───────────────────────────────────────


def _call_mcp_with_extra(*requests, config=None, project_root=None, extra_tools=None):
    """Run the repo_cmd MCP server with --extra-tools."""
    from repo_tools.agent.mcp.repo_cmd import main

    if config is None:
        config = {}
    if project_root is None:
        project_root = "/tmp/test"

    argv = [
        "repo_cmd_mcp",
        "--project-root", str(project_root),
        "--config", json.dumps(config),
    ]
    if extra_tools:
        argv.extend(["--extra-tools", json.dumps(extra_tools)])

    lines = "\n".join(json.dumps(r) for r in requests) + "\n"
    captured = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(lines)),
        patch("sys.stdout", captured),
        patch("sys.argv", argv),
    ):
        main()
    output = captured.getvalue().strip()
    if not output:
        return []
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_mcp_extra_tools_in_list():
    extra = [{"name": "clean", "description": "Clean up"}]
    responses = _call_mcp_with_extra(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        extra_tools=extra,
    )
    tools = responses[0]["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "repo_run"
    enum = tools[0]["inputSchema"]["properties"]["command"]["enum"]
    assert "clean" in enum


def test_mcp_extra_tools_callable(tmp_path):
    extra = [{"name": "clean", "description": "Clean up"}]
    mock_proc = MagicMock()
    mock_proc.stdout = "cleaned"
    mock_proc.stderr = ""
    mock_proc.returncode = 0

    with patch("subprocess.run", return_value=mock_proc):
        responses = _call_mcp_with_extra(
            {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "repo_run", "arguments": {"command": "clean"}},
            },
            project_root=str(tmp_path),
            extra_tools=extra,
        )
    result = responses[0]["result"]
    assert "cleaned" in result["content"][0]["text"]
