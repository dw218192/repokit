"""Tests for the lint_mcp stdio MCP server."""

from __future__ import annotations

import io
import json
import subprocess
from unittest.mock import MagicMock, patch

from repo_tools.agent.hooks.lint_mcp_stdio import main

_MOD = "repo_tools.agent.hooks.lint_mcp_stdio"
_LINT_MOD = "repo_tools.agent.lint"


# ── Helper ────────────────────────────────────────────────────────────────────


def _call(*requests: dict, cli_args: list[str] | None = None) -> list[dict]:
    """Run main() with the given requests, return parsed JSON responses."""
    lines = "\n".join(json.dumps(r) for r in requests) + "\n"
    captured = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(lines)),
        patch("sys.stdout", captured),
        patch("sys.argv", ["lint_mcp_stdio", *(cli_args or [])]),
    ):
        main()
    output = captured.getvalue().strip()
    if not output:
        return []
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def _lint_call(args: dict | None = None, **kw) -> list[dict]:
    """Shorthand: call the lint tool with given arguments."""
    if args is None:
        args = {}
    return _call(
        {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "lint", "arguments": args},
        },
        **kw,
    )


# ── Protocol tests ────────────────────────────────────────────────────────────


def test_initialize():
    responses = _call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "lint"


def test_tools_list():
    responses = _call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(responses) == 1
    tools = responses[0]["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names == ["lint"]


def test_ping():
    responses = _call({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert len(responses) == 1
    assert responses[0]["result"] == {}


def test_notification_no_response():
    responses = _call({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert responses == []


def test_unknown_method():
    responses = _call({"jsonrpc": "2.0", "id": 4, "method": "some/unknown"})
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == -32601


def test_invalid_json_skipped():
    lines = 'not-valid-json\n{"jsonrpc":"2.0","id":5,"method":"ping"}\n'
    captured = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(lines)),
        patch("sys.stdout", captured),
        patch("sys.argv", ["lint_mcp_stdio"]),
    ):
        main()
    output = captured.getvalue().strip()
    responses = [json.loads(line) for line in output.splitlines() if line.strip()]
    assert len(responses) == 1
    assert responses[0]["result"] == {}


# ── Lint tool: Python file ───────────────────────────────────────────────────


def test_lint_python_file(tmp_path):
    """Lint on a .py file dispatches to ruff."""
    py_file = tmp_path / "app.py"
    py_file.write_text("x = 1\n")

    mock_proc = MagicMock()
    mock_proc.stdout = "app.py:1:1: F841 local variable 'x' is assigned to but never used\n"
    mock_proc.stderr = ""

    with (
        patch(f"{_LINT_MOD}._find_executable", return_value="/usr/bin/ruff"),
        patch("subprocess.run", return_value=mock_proc),
    ):
        responses = _lint_call({"path": str(py_file)})

    assert len(responses) == 1
    result = responses[0]["result"]
    assert result.get("isError") is None
    assert "F841" in result["content"][0]["text"]


def test_lint_python_no_issues(tmp_path):
    py_file = tmp_path / "clean.py"
    py_file.write_text("x = 1\n")

    mock_proc = MagicMock()
    mock_proc.stdout = ""
    mock_proc.stderr = ""

    with (
        patch(f"{_LINT_MOD}._find_executable", return_value="/usr/bin/ruff"),
        patch("subprocess.run", return_value=mock_proc),
    ):
        responses = _lint_call({"path": str(py_file)})

    text = responses[0]["result"]["content"][0]["text"]
    assert "No issues" in text


def test_lint_ruff_not_installed(tmp_path):
    py_file = tmp_path / "app.py"
    py_file.write_text("x = 1\n")

    with patch(f"{_LINT_MOD}._find_executable", return_value=None):
        responses = _lint_call({"path": str(py_file)})

    text = responses[0]["result"]["content"][0]["text"]
    assert "not installed" in text


def test_lint_ruff_timeout(tmp_path):
    py_file = tmp_path / "app.py"
    py_file.write_text("x = 1\n")

    with (
        patch(f"{_LINT_MOD}._find_executable", return_value="/usr/bin/ruff"),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ruff", timeout=120),
        ),
    ):
        responses = _lint_call({"path": str(py_file)})

    text = responses[0]["result"]["content"][0]["text"]
    assert "timed out" in text


# ── Lint tool: C++ file ──────────────────────────────────────────────────────


def test_lint_cpp_file(tmp_path):
    """Lint on a .cpp file dispatches to clang-tidy."""
    cpp_file = tmp_path / "main.cpp"
    cpp_file.write_text("int main() { return 0; }\n")

    mock_proc = MagicMock()
    mock_proc.stdout = "1 warning generated.\n"
    mock_proc.stderr = ""

    with (
        patch(f"{_LINT_MOD}._find_executable", return_value="/usr/bin/clang-tidy"),
        patch("subprocess.run", return_value=mock_proc),
    ):
        responses = _lint_call({"path": str(tmp_path)})

    result = responses[0]["result"]
    assert result.get("isError") is None
    assert "warning" in result["content"][0]["text"]


def test_lint_clang_tidy_not_installed(tmp_path):
    cpp_file = tmp_path / "main.cpp"
    cpp_file.write_text("int main() {}\n")

    with patch(f"{_LINT_MOD}._find_executable", return_value=None):
        responses = _lint_call({"path": str(tmp_path)})

    text = responses[0]["result"]["content"][0]["text"]
    assert "not installed" in text


# ── Lint tool: mixed directory ───────────────────────────────────────────────


def test_lint_mixed_directory(tmp_path):
    """Directory with both .py and .cpp runs both linters."""
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "main.cpp").write_text("int main() {}\n")

    ruff_proc = MagicMock()
    ruff_proc.stdout = "app.py:1: F841 unused\n"
    ruff_proc.stderr = ""
    clang_proc = MagicMock()
    clang_proc.stdout = "1 warning\n"
    clang_proc.stderr = ""

    def _find(name):
        return f"/usr/bin/{name}"

    def _run(cmd, **kw):
        if "ruff" in cmd[0]:
            return ruff_proc
        return clang_proc

    with (
        patch(f"{_LINT_MOD}._find_executable", side_effect=_find),
        patch("subprocess.run", side_effect=_run),
    ):
        responses = _lint_call({"path": str(tmp_path)})

    text = responses[0]["result"]["content"][0]["text"]
    assert "F841" in text
    assert "warning" in text


# ── Lint tool: clang-tidy compile_commands auto-detect ────────────────────────


def test_clang_tidy_finds_compile_commands(tmp_path):
    """clang-tidy auto-detects compile_commands.json by searching upward."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "compile_commands.json").write_text("[]\n")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.cpp").write_text("int main() {}\n")

    captured_cmds: list = []

    def _fake_run(cmd, **kw):
        captured_cmds.append(cmd)
        mock = MagicMock()
        mock.stdout = "ok"
        mock.stderr = ""
        return mock

    with (
        patch(f"{_LINT_MOD}._find_executable", return_value="/usr/bin/clang-tidy"),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        _lint_call({"path": str(src_dir)})

    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    assert "-p" in cmd
    p_idx = cmd.index("-p")
    assert cmd[p_idx + 1] == str(build_dir)


def test_clang_tidy_no_compile_commands(tmp_path):
    """Without compile_commands.json, -p is not passed."""
    (tmp_path / "main.cpp").write_text("int main() {}\n")

    captured_cmds: list = []

    def _fake_run(cmd, **kw):
        captured_cmds.append(cmd)
        mock = MagicMock()
        mock.stdout = "ok"
        mock.stderr = ""
        return mock

    with (
        patch(f"{_LINT_MOD}._find_executable", return_value="/usr/bin/clang-tidy"),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        _lint_call({"path": str(tmp_path)})

    assert len(captured_cmds) == 1
    assert "-p" not in captured_cmds[0]


# ── Lint tool: no lintable files ─────────────────────────────────────────────


def test_lint_no_lintable_files(tmp_path):
    (tmp_path / "readme.md").write_text("# Hello\n")
    responses = _lint_call({"path": str(tmp_path)})

    text = responses[0]["result"]["content"][0]["text"]
    assert "No lintable files" in text


def test_lint_nonexistent_path():
    responses = _lint_call({"path": "/nonexistent/path/12345"})
    result = responses[0]["result"]
    assert result.get("isError") is True
    assert "does not exist" in result["content"][0]["text"]


# ── Lint tool: ruff config passthrough ───────────────────────────────────────


def test_lint_default_select_passed_through(tmp_path):
    """--select CLI arg flows through to ruff command."""
    py_file = tmp_path / "app.py"
    py_file.write_text("x = 1\n")
    captured_cmds: list = []

    def _fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        mock = MagicMock()
        mock.stdout = "ok"
        mock.stderr = ""
        return mock

    with (
        patch(f"{_LINT_MOD}._find_executable", return_value="/usr/bin/ruff"),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        _lint_call({"path": str(py_file)}, cli_args=["--select", "S,B"])

    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    select_idx = cmd.index("--select")
    assert cmd[select_idx + 1] == "S,B"


def test_lint_default_ignore_passed_through(tmp_path):
    """--ignore CLI arg flows through to ruff command."""
    py_file = tmp_path / "app.py"
    py_file.write_text("x = 1\n")
    captured_cmds: list = []

    def _fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        mock = MagicMock()
        mock.stdout = "ok"
        mock.stderr = ""
        return mock

    with (
        patch(f"{_LINT_MOD}._find_executable", return_value="/usr/bin/ruff"),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        _lint_call({"path": str(py_file)}, cli_args=["--ignore", "SIM108"])

    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    assert "--ignore" in cmd
    ignore_idx = cmd.index("--ignore")
    assert cmd[ignore_idx + 1] == "SIM108"


def test_lint_builtin_default_select(tmp_path):
    """With no CLI args, built-in default select is used."""
    py_file = tmp_path / "app.py"
    py_file.write_text("x = 1\n")
    captured_cmds: list = []

    def _fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        mock = MagicMock()
        mock.stdout = "ok"
        mock.stderr = ""
        return mock

    with (
        patch(f"{_LINT_MOD}._find_executable", return_value="/usr/bin/ruff"),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        _lint_call({"path": str(py_file)})

    cmd = captured_cmds[0]
    select_idx = cmd.index("--select")
    assert cmd[select_idx + 1] == "F,S110,S301,S307,S602,B,SIM"


# ── Unknown tool ─────────────────────────────────────────────────────────────


def test_unknown_tool():
    responses = _call({
        "jsonrpc": "2.0", "id": 30, "method": "tools/call",
        "params": {"name": "nonexistent", "arguments": {}},
    })
    result = responses[0]["result"]
    assert result.get("isError") is True
    assert "Unknown tool" in result["content"][0]["text"]


# ── Exception handling ───────────────────────────────────────────────────────


def test_dispatch_exception_with_id_returns_error():
    captured = io.StringIO()
    captured_stderr = io.StringIO()
    req = {
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": "lint", "arguments": {}},
    }
    lines = json.dumps(req) + "\n"

    with (
        patch("sys.stdin", io.StringIO(lines)),
        patch("sys.stdout", captured),
        patch("sys.stderr", captured_stderr),
        patch("sys.argv", ["lint_mcp_stdio"]),
        patch(f"{_MOD}._dispatch", side_effect=RuntimeError("boom")),
    ):
        main()

    output = captured.getvalue().strip()
    responses = [json.loads(line) for line in output.splitlines() if line.strip()]
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == -32603
    assert "boom" in captured_stderr.getvalue()


def test_dispatch_exception_on_notification_no_response():
    captured = io.StringIO()
    captured_stderr = io.StringIO()
    req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    lines = json.dumps(req) + "\n"

    with (
        patch("sys.stdin", io.StringIO(lines)),
        patch("sys.stdout", captured),
        patch("sys.stderr", captured_stderr),
        patch("sys.argv", ["lint_mcp_stdio"]),
        patch(f"{_MOD}._dispatch", side_effect=RuntimeError("silent")),
    ):
        main()

    output = captured.getvalue().strip()
    assert output == ""
    assert "silent" in captured_stderr.getvalue()
