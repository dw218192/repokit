"""Tests for uncovered areas in repo_tools.core."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from repo_tools.core import (
    ShellCommand,
    TokenFormatter,
    detect_platform_identifier,
    find_venv_executable,
    _map_platform_identifier,
    invoke_tool,
    log_section,
    print_subprocess_line,
    register_tool,
    remove_tree_with_retries,
    resolve_path,
    resolve_tokens,
    RepoTool,
    ToolContext,
)


# ── detect_platform_identifier ────────────────────────────────────


class TestDetectPlatform:
    def test_override(self):
        assert detect_platform_identifier("custom-platform") == "custom-platform"

    @patch("repo_tools.core.platform")
    def test_windows_x64(self, mock_platform):
        mock_platform.system.return_value = "Windows"
        mock_platform.machine.return_value = "AMD64"
        assert detect_platform_identifier() == "windows-x64"

    @patch("repo_tools.core.platform")
    def test_linux_arm64(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "aarch64"
        assert detect_platform_identifier() == "linux-arm64"

    @patch("repo_tools.core.platform")
    def test_macos_x64(self, mock_platform):
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "x86_64"
        assert detect_platform_identifier() == "macos-x64"

    @patch("repo_tools.core.platform")
    def test_unknown_os(self, mock_platform):
        mock_platform.system.return_value = "FreeBSD"
        mock_platform.machine.return_value = "x86_64"
        assert detect_platform_identifier() == "freebsd-x64"

    @patch("repo_tools.core.platform")
    def test_unknown_arch(self, mock_platform):
        mock_platform.system.return_value = "Linux"
        mock_platform.machine.return_value = "riscv64"
        assert detect_platform_identifier() == "linux-riscv64"

    def test_conan_profile(self, tmp_path):
        profile = tmp_path / "profile"
        profile.write_text("os=Linux\narch=x86_64\n", encoding="utf-8")
        result = detect_platform_identifier(conan_profile_path=profile)
        assert result == "linux-x64"


# ── _map_platform_identifier ─────────────────────────────────────


class TestMapPlatform:
    def test_emscripten(self):
        assert _map_platform_identifier("Emscripten", "wasm") == "emscripten"

    def test_windows_x86(self):
        assert _map_platform_identifier("Windows", "x86") == "windows-x86"

    def test_macos_armv8(self):
        assert _map_platform_identifier("Macos", "armv8") == "macos-arm64"

    def test_darwin_alias(self):
        assert _map_platform_identifier("Darwin", "x86_64") == "macos-x64"

    def test_unknown_values(self):
        assert _map_platform_identifier("FooOS", "bar_arch") == "fooos-bar_arch"


# ── resolve_tokens edge cases ─────────────────────────────────────


class TestResolveTokensEdge:
    def test_list_tokens_skipped(self):
        config = {"repo": {"tokens": {"platform": ["linux-x64", "windows-x64"], "custom": "val"}}}
        result = resolve_tokens("/ws", config, {"platform": "linux-x64", "build_type": "Debug"})
        assert result["custom"] == "val"
        # list token should not appear as raw list
        assert isinstance(result.get("platform"), str)


# ── invoke_tool ───────────────────────────────────────────────────


class TestInvokeTool:
    def test_invoke_registered_tool(self):
        tool = MagicMock(spec=RepoTool)
        tool.name = "test_invoke"
        tool.default_args.return_value = {"key": "default"}
        register_tool(tool)

        tokens = {"workspace_root": "/tmp"}
        invoke_tool("test_invoke", tokens, config={"test_invoke": {"cmd": "echo"}})
        tool.execute.assert_called_once()

    def test_invoke_nonexistent_raises(self):
        with pytest.raises(KeyError, match="not_registered"):
            invoke_tool("not_registered", {}, config={})

    def test_invoke_with_extra_args(self):
        tool = MagicMock(spec=RepoTool)
        tool.name = "test_extra"
        tool.default_args.return_value = {}
        register_tool(tool)

        invoke_tool("test_extra", {"workspace_root": "/tmp"}, config={},
                     extra_args={"verbose": True})
        args = tool.execute.call_args[0][1]
        assert args["verbose"] is True


# ── remove_tree_with_retries ──────────────────────────────────────


class TestRemoveTreeWithRetries:
    def test_removes_on_first_attempt(self, tmp_path):
        d = tmp_path / "target"
        d.mkdir()
        (d / "file.txt").write_text("hi", encoding="utf-8")
        remove_tree_with_retries(d, attempts=3, delay=0)
        assert not d.exists()

    @patch("repo_tools.core.shutil.rmtree")
    @patch("repo_tools.core.time.sleep")
    def test_retries_on_permission_error(self, mock_sleep, mock_rmtree):
        mock_rmtree.side_effect = [PermissionError, PermissionError, None]
        remove_tree_with_retries(Path("/tmp/fake"), attempts=3, delay=0.01)
        assert mock_rmtree.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("repo_tools.core.shutil.rmtree")
    def test_raises_after_all_retries(self, mock_rmtree):
        mock_rmtree.side_effect = PermissionError("locked")
        with pytest.raises(PermissionError):
            remove_tree_with_retries(Path("/tmp/fake"), attempts=2, delay=0)


# ── resolve_path ──────────────────────────────────────────────────


class TestResolvePath:
    def test_absolute_path(self):
        result = resolve_path(Path("/root"), "/abs/path", {})
        assert result == Path("/abs/path")

    def test_relative_path_joined(self):
        result = resolve_path(Path("/root"), "sub/dir", {})
        assert result == Path("/root/sub/dir")

    def test_token_expansion(self):
        result = resolve_path(Path("/root"), "{name}/out", {"name": "proj"})
        assert result == Path("/root/proj/out")


# ── log_section ───────────────────────────────────────────────────


class TestLogSection:
    @patch.dict("os.environ", {"GITHUB_ACTIONS": "true"})
    def test_ci_mode(self, capsys):
        with log_section("My Section"):
            print("inside")
        captured = capsys.readouterr()
        assert "::group::My Section" in captured.out
        assert "::endgroup::" in captured.out

    @patch.dict("os.environ", {}, clear=True)
    def test_local_mode(self, capture_logs):
        import os
        os.environ.pop("GITHUB_ACTIONS", None)
        buf = capture_logs
        with log_section("Local"):
            pass
        assert "Local" in buf.getvalue()


# ── print_subprocess_line ─────────────────────────────────────────


class TestPrintSubprocessLine:
    def test_prints_dimmed(self, capsys):
        print_subprocess_line("hello world\n")
        captured = capsys.readouterr()
        assert "hello world" in captured.out


class TestFindVenvExecutable:
    def test_finds_in_venv(self):
        # Just verify it returns a string and doesn't crash
        result = find_venv_executable("python")
        assert isinstance(result, str)

    @patch("repo_tools.core.shutil.which", return_value=None)
    def test_fallback_returns_name(self, mock_which):
        # Clear the cache for this test
        find_venv_executable.cache_clear()
        result = find_venv_executable("nonexistent_tool_xyz")
        assert "nonexistent_tool_xyz" in result
        find_venv_executable.cache_clear()


# ── ShellCommand shell quoting ────────────────────────────────


class TestShellCommandQuoting:
    """ShellCommand uses shlex.join on Unix, list2cmdline on Windows."""

    @patch("repo_tools.core.is_windows", return_value=False)
    @patch("repo_tools.core.subprocess.run")
    def test_unix_uses_shlex_join(self, mock_run, _mock_win, tmp_path):
        """On Unix the env_script path uses shlex.join (single-quotes)."""
        script = tmp_path / "env.sh"
        script.write_text("# env", encoding="utf-8")

        sc = ShellCommand(["echo", "hello world"], env_script=script)
        mock_run.return_value = MagicMock(returncode=0)
        sc.run()

        cmd_str = mock_run.call_args[0][0]
        assert "echo 'hello world'" in cmd_str or 'echo "hello world"' in cmd_str or "echo hello\\ world" in cmd_str

    @patch("repo_tools.core.is_windows", return_value=True)
    @patch("repo_tools.core.subprocess.run")
    def test_windows_uses_list2cmdline(self, mock_run, _mock_win, tmp_path):
        """On Windows the env_script path uses subprocess.list2cmdline."""
        import subprocess as sp
        script = tmp_path / "env.bat"
        script.write_text("REM env", encoding="utf-8")

        sc = ShellCommand(["echo", "hello world"], env_script=script)
        mock_run.return_value = MagicMock(returncode=0)
        sc.run()

        cmd_str = mock_run.call_args[0][0]
        assert "call" in cmd_str
        expected = sp.list2cmdline(["echo", "hello world"])
        assert expected in cmd_str


class TestShellCommandCwd:
    """ShellCommand passes cwd to subprocess."""

    @patch("repo_tools.core.subprocess.run")
    def test_cwd_passed_to_subprocess_run(self, mock_run, tmp_path):
        sc = ShellCommand(["echo", "hi"], cwd=tmp_path)
        sc.run()
        assert mock_run.call_args[1]["cwd"] == tmp_path

    @patch("repo_tools.core.subprocess.Popen")
    def test_cwd_passed_to_popen(self, mock_popen, tmp_path):
        sc = ShellCommand(["echo", "hi"], cwd=tmp_path)
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        sc.popen()
        assert mock_popen.call_args[1]["cwd"] == tmp_path


class TestShellCommandExecFailLoud:
    """ShellCommand.exec() errors out when env_script doesn't exist."""

    def test_missing_env_script_exits(self, tmp_path):
        missing = tmp_path / "nonexistent.sh"
        sc = ShellCommand(["echo", "hi"], env_script=missing)
        with pytest.raises(SystemExit):
            sc.exec()

    def test_missing_env_script_auto_suffix(self, tmp_path):
        """Auto-suffixed env_script that doesn't exist also fails."""
        missing = tmp_path / "nonexistent"  # no suffix — will try .bat/.sh
        sc = ShellCommand(["echo", "hi"], env_script=missing)
        with pytest.raises(SystemExit):
            sc.exec()


class TestIsWindows:
    @patch("repo_tools.core.platform.system", return_value="Windows")
    def test_windows(self, mock_sys):
        from repo_tools.core import is_windows
        assert is_windows() is True

    @patch("repo_tools.core.platform.system", return_value="Linux")
    def test_linux(self, mock_sys):
        from repo_tools.core import is_windows
        assert is_windows() is False


# ── ShellCommand env parameter ────────────────────────────────


class TestShellCommandEnv:
    @patch("repo_tools.core.subprocess.run")
    def test_env_passed_to_subprocess_run(self, mock_run):
        custom_env = {"MY_VAR": "hello"}
        sc = ShellCommand(["echo", "hi"], env=custom_env)
        sc.run()
        call_env = mock_run.call_args[1]["env"]
        assert call_env["MY_VAR"] == "hello"
        # Should also contain os.environ entries
        assert "PATH" in call_env or len(call_env) > 1

    @patch("repo_tools.core.subprocess.Popen")
    def test_env_passed_to_popen(self, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        custom_env = {"MY_VAR": "world"}
        sc = ShellCommand(["echo", "hi"], env=custom_env)
        sc.exec(log_file=tmp_path / "log.txt")
        call_env = mock_popen.call_args[1]["env"]
        assert call_env["MY_VAR"] == "world"

    @patch("repo_tools.core.subprocess.run")
    def test_env_none_no_override(self, mock_run):
        sc = ShellCommand(["echo", "hi"])
        sc.run()
        assert mock_run.call_args[1]["env"] is None


# ── CommandGroup env parameter ────────────────────────────────


class TestCommandGroupEnv:
    @patch("repo_tools.core.ShellCommand")
    def test_group_env_forwarded(self, MockSC):
        from repo_tools.core import CommandGroup
        with CommandGroup("test", env={"A": "1"}) as g:
            g.run(["echo", "hi"])
        assert MockSC.call_args[1]["env"] == {"A": "1"}

    @patch("repo_tools.core.ShellCommand")
    def test_per_step_env_overrides_group(self, MockSC):
        from repo_tools.core import CommandGroup
        with CommandGroup("test", env={"A": "1", "B": "2"}) as g:
            g.run(["echo", "hi"], env={"B": "override", "C": "3"})
        merged = MockSC.call_args[1]["env"]
        assert merged == {"A": "1", "B": "override", "C": "3"}

    @patch("repo_tools.core.ShellCommand")
    def test_no_env_passes_none(self, MockSC):
        from repo_tools.core import CommandGroup
        with CommandGroup("test") as g:
            g.run(["echo", "hi"])
        assert MockSC.call_args[1]["env"] is None
