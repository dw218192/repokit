"""Tests for uncovered paths in FormatTool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from repo_tools.format import FormatTool


class TestConfiguredBackends:
    def test_clang_format_backend(self, tmp_path, make_tool_context):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".clang-format").write_text("BasedOnStyle: Google\n")
        (ws / "main.cpp").write_text("int main() {}\n")

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {
            "verify": False,
            "backends": [{"type": "clang-format"}],
        }

        with (
            patch("repo_tools.format.subprocess.run") as mock_run,
            patch("repo_tools.format.shutil.which", return_value="/usr/bin/clang-format"),
            patch("repo_tools.format.find_venv_executable", return_value="clang-format"),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            tool.execute(ctx, args)
            mock_run.assert_called()

    def test_python_backend(self, tmp_path, make_tool_context):
        ws = tmp_path / "ws"
        ws.mkdir()

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {
            "verify": False,
            "backends": [{"type": "python", "tool": "ruff"}],
        }

        with (
            patch("repo_tools.format.subprocess.run") as mock_run,
            patch("repo_tools.format.shutil.which", return_value="/usr/bin/ruff"),
            patch("repo_tools.format.find_venv_executable", return_value="ruff"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            tool.execute(ctx, args)
            mock_run.assert_called()

    def test_unknown_backend_warns(self, tmp_path, make_tool_context, capture_logs):
        ws = tmp_path / "ws"
        ws.mkdir()

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {
            "verify": False,
            "backends": [{"type": "unknown_thing"}],
        }
        buf = capture_logs
        tool.execute(ctx, args)
        assert "Unknown format backend" in buf.getvalue()


class TestNoSourceFiles:
    def test_no_files_warns(self, tmp_path, make_tool_context, capture_logs):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".clang-format").write_text("BasedOnStyle: Google\n")
        # No source files

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {"verify": False}
        buf = capture_logs

        with (
            patch("repo_tools.format.shutil.which", return_value="/usr/bin/clang-format"),
            patch("repo_tools.format.find_venv_executable", return_value="clang-format"),
        ):
            tool.execute(ctx, args)
        assert "No source files" in buf.getvalue()


class TestPythonFormatterErrors:
    def test_python_formatter_not_found(self, tmp_path, make_tool_context):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "pyproject.toml").write_text("[project]\nname='x'\n")

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {"verify": False}

        with (
            patch("repo_tools.format.shutil.which", return_value=None),
            patch("repo_tools.format.find_venv_executable", return_value="ruff"),
            pytest.raises(SystemExit),
        ):
            tool.execute(ctx, args)

    def test_python_verify_mode(self, tmp_path, make_tool_context):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "pyproject.toml").write_text("[project]\nname='x'\n")

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {"verify": True}

        with (
            patch("repo_tools.format.subprocess.run") as mock_run,
            patch("repo_tools.format.shutil.which", return_value="/usr/bin/ruff"),
            patch("repo_tools.format.find_venv_executable", return_value="ruff"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            tool.execute(ctx, args)
            cmd = mock_run.call_args[0][0]
            assert "--check" in cmd

    def test_python_format_failure(self, tmp_path, make_tool_context):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "pyproject.toml").write_text("[project]\nname='x'\n")

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {"verify": False}

        import subprocess
        with (
            patch("repo_tools.format.subprocess.run", side_effect=subprocess.CalledProcessError(1, "ruff")),
            patch("repo_tools.format.shutil.which", return_value="/usr/bin/ruff"),
            patch("repo_tools.format.find_venv_executable", return_value="ruff"),
            pytest.raises(SystemExit),
        ):
            tool.execute(ctx, args)

    def test_python_verify_failure(self, tmp_path, make_tool_context):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "pyproject.toml").write_text("[project]\nname='x'\n")

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {"verify": True}

        import subprocess
        with (
            patch("repo_tools.format.subprocess.run", side_effect=subprocess.CalledProcessError(1, "ruff")),
            patch("repo_tools.format.shutil.which", return_value="/usr/bin/ruff"),
            patch("repo_tools.format.find_venv_executable", return_value="ruff"),
            pytest.raises(SystemExit),
        ):
            tool.execute(ctx, args)


class TestFormatDefaults:
    def test_default_args(self):
        tool = FormatTool()
        args = tool.default_args({})
        assert args == {"verify": False}


class TestMissingClangFormatConfig:
    def test_no_clang_format_file_exits(self, tmp_path, make_tool_context):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "main.cpp").write_text("int main() {}\n")
        # No .clang-format file

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {
            "verify": False,
            "backends": [{"type": "clang-format"}],
        }

        with (
            patch("repo_tools.format.shutil.which", return_value="/usr/bin/clang-format"),
            patch("repo_tools.format.find_venv_executable", return_value="clang-format"),
            pytest.raises(SystemExit),
        ):
            tool.execute(ctx, args)


class TestClangFormatInplaceError:
    def test_inplace_failure(self, tmp_path, make_tool_context):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".clang-format").write_text("BasedOnStyle: Google\n")
        (ws / "main.cpp").write_text("int main() {}\n")

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {"verify": False}

        import subprocess
        with (
            patch("repo_tools.format.subprocess.run", side_effect=subprocess.CalledProcessError(1, "clang-format", stderr="error")),
            patch("repo_tools.format.shutil.which", return_value="/usr/bin/clang-format"),
            patch("repo_tools.format.find_venv_executable", return_value="clang-format"),
            pytest.raises(SystemExit),
        ):
            tool.execute(ctx, args)
