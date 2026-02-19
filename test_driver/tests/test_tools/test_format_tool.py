"""Tests for FormatTool (repo_tools.format)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from repo_tools.format import FormatTool


class TestFormatTool:
    """Unit tests for FormatTool.execute()."""

    def test_auto_detect_clang_format(self, tmp_path, make_tool_context):
        """When .clang-format exists with source files, clang-format is invoked."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".clang-format").write_text("BasedOnStyle: Google\n")
        (ws / "main.cpp").write_text("int main() { return 0; }\n")

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {"verify": False}

        with (
            patch("repo_tools.format.subprocess.run") as mock_run,
            patch("repo_tools.format.shutil.which", return_value="/usr/bin/clang-format"),
            patch("repo_tools.format.find_venv_executable", return_value="clang-format"),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            tool.execute(ctx, args)

            mock_run.assert_called()
            # At least one call should contain clang-format with -i (in-place formatting)
            calls = mock_run.call_args_list
            clang_calls = [
                c for c in calls
                if any("clang-format" in str(a) for a in c[0][0][:1])
            ]
            assert len(clang_calls) > 0

    def test_auto_detect_python(self, tmp_path, make_tool_context):
        """When pyproject.toml exists, ruff formatter is invoked."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "pyproject.toml").write_text("[project]\nname = 'test'\n")

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {"verify": False}

        with (
            patch("repo_tools.format.subprocess.run") as mock_run,
            patch("repo_tools.format.find_venv_executable", return_value="ruff"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            tool.execute(ctx, args)

            mock_run.assert_called()
            # Verify ruff was called with "format" subcommand
            calls = mock_run.call_args_list
            ruff_calls = [
                c for c in calls
                if "ruff" in str(c[0][0][0]) and "format" in c[0][0]
            ]
            assert len(ruff_calls) > 0

    def test_missing_clang_format_exits(self, tmp_path, make_tool_context):
        """If clang-format is not found on PATH, SystemExit is raised."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".clang-format").write_text("BasedOnStyle: Google\n")
        (ws / "main.cpp").write_text("int main() {}\n")

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {"verify": False}

        with (
            patch("repo_tools.format.shutil.which", return_value=None),
            patch("repo_tools.format.find_venv_executable", return_value="clang-format"),
            pytest.raises(SystemExit),
        ):
            tool.execute(ctx, args)

    def test_verify_mode(self, tmp_path, make_tool_context):
        """verify=True uses --dry-run --Werror args for clang-format."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".clang-format").write_text("BasedOnStyle: Google\n")
        (ws / "main.cpp").write_text("int main() { return 0; }\n")

        ctx = make_tool_context(workspace_root=ws)
        tool = FormatTool()
        args = {"verify": True}

        with (
            patch("repo_tools.format.subprocess.run") as mock_run,
            patch("repo_tools.format.shutil.which", return_value="/usr/bin/clang-format"),
            patch("repo_tools.format.find_venv_executable", return_value="clang-format"),
        ):
            # First call tests whether --dry-run is supported; return 0 means supported
            # Second call is the actual verify check; return 0 means all files pass
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            tool.execute(ctx, args)

            mock_run.assert_called()
            calls = mock_run.call_args_list
            # At least one call should contain --dry-run or compare file contents
            dry_run_calls = [
                c for c in calls
                if "--dry-run" in c[0][0] or "--Werror" in c[0][0]
            ]
            assert len(dry_run_calls) > 0, (
                "verify mode should use --dry-run/--Werror or per-file comparison"
            )
