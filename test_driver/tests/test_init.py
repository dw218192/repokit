"""Tests for InitTool (repo init)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from repo_tools.init import InitTool, _find_uv


@pytest.fixture
def init_ctx(make_tool_context, tmp_path):
    """Build a ToolContext with framework and project requirements files."""
    ws = tmp_path / "project"
    ws.mkdir()

    # Framework requirements (lives under the framework_root token)
    fw_root = tmp_path / "framework"
    fw_root.mkdir()
    (fw_root / "requirements.txt").write_text("click>=8.0\n")

    # Project requirements
    tools_dir = ws / "tools"
    tools_dir.mkdir()
    (tools_dir / "requirements.txt").write_text("pytest>=7.0\n")

    return make_tool_context(
        workspace_root=ws,
        tokens_override={"framework_root": str(fw_root)},
    )


class TestFindUv:
    def test_finds_uv_in_tools_bin(self, tmp_path):
        ws = tmp_path / "project"
        tools_bin = ws / "_tools" / "bin"
        tools_bin.mkdir(parents=True)
        suffix = ".exe" if sys.platform == "win32" else ""
        uv = tools_bin / f"uv{suffix}"
        uv.write_text("fake")

        assert _find_uv(ws) == str(uv)

    @patch("repo_tools.init.find_venv_executable", return_value="/venv/bin/uv")
    @patch("repo_tools.init.shutil.which", return_value="/venv/bin/uv")
    def test_falls_back_to_venv(self, _which, _find, tmp_path):
        ws = tmp_path / "project"
        ws.mkdir()
        assert _find_uv(ws) == "/venv/bin/uv"

    @patch("repo_tools.init.find_venv_executable", return_value="uv")
    @patch("repo_tools.init.shutil.which", return_value=None)
    def test_returns_none_when_missing(self, _which, _find, tmp_path):
        ws = tmp_path / "project"
        ws.mkdir()
        assert _find_uv(ws) is None


class TestInitTool:
    @patch("repo_tools.init.subprocess.run")
    @patch("repo_tools.init._find_uv", return_value="/bin/uv")
    def test_installs_framework_and_project(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=0)
        tool = InitTool()

        tool.execute(init_ctx, {})

        assert mock_run.call_count == 2
        fw_call, proj_call = mock_run.call_args_list
        assert "-r" in fw_call.args[0]
        assert "requirements.txt" in fw_call.args[0][-1]
        assert "requirements.txt" in proj_call.args[0][-1]

    @patch("repo_tools.init.subprocess.run")
    @patch("repo_tools.init._find_uv", return_value="/bin/uv")
    def test_skips_missing_project_requirements(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=0)
        # Remove project requirements
        proj_reqs = init_ctx.workspace_root / "tools" / "requirements.txt"
        proj_reqs.unlink()

        tool = InitTool()
        tool.execute(init_ctx, {})

        assert mock_run.call_count == 1

    @patch("repo_tools.init.subprocess.run")
    @patch("repo_tools.init._find_uv", return_value="/bin/uv")
    def test_exits_on_failure(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=1)
        tool = InitTool()

        with pytest.raises(SystemExit) as exc_info:
            tool.execute(init_ctx, {})
        assert exc_info.value.code == 1

    @patch("repo_tools.init._find_uv", return_value=None)
    def test_exits_when_uv_missing(self, _uv, init_ctx):
        tool = InitTool()

        with pytest.raises(SystemExit) as exc_info:
            tool.execute(init_ctx, {})
        assert exc_info.value.code == 1

    @patch("repo_tools.init.subprocess.run")
    @patch("repo_tools.init._find_uv", return_value="/bin/uv")
    def test_patches_gitignore(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=0)
        tool = InitTool()

        tool.execute(init_ctx, {})

        gitignore = init_ctx.workspace_root / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert "_tools/" in content
        assert "repo" in content
