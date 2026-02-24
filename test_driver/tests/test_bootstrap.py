"""Tests for bootstrap root detection and _is_local_venv guard."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from repo_tools._bootstrap import derive_project_root
from repo_tools.init import _is_local_venv


def _git(*args: str, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    """Run a git command with test-safe defaults."""
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@test.com",
         "-c", "init.defaultBranch=main",
         "-c", "protocol.file.allow=always"] + list(args),
        cwd=cwd, capture_output=True, text=True,
    )


def _git_init(path: Path) -> Path:
    """Initialize a git repo with an initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    (path / ".gitkeep").write_text("")
    _git("add", ".", cwd=path)
    _git("commit", "-m", "init", cwd=path)
    return path


def _norm(p: Path) -> str:
    """Normalize path for cross-platform comparison."""
    return os.path.normcase(os.path.normpath(str(p)))


# ── _is_local_venv ──────────────────────────────────────────────────────────


class TestIsLocalVenv:
    def test_python_in_workspace_venv_unix(self, tmp_path, monkeypatch):
        ws = tmp_path / "project"
        ws.mkdir()
        monkeypatch.setattr(sys, "executable", str(ws / "_tools/venv/bin/python"))
        assert _is_local_venv(ws) is True

    def test_python_in_workspace_venv_win(self, tmp_path, monkeypatch):
        ws = tmp_path / "project"
        ws.mkdir()
        monkeypatch.setattr(sys, "executable", str(ws / "_tools/venv/Scripts/python.exe"))
        assert _is_local_venv(ws) is True

    def test_python_outside_workspace(self, tmp_path, monkeypatch):
        ws = tmp_path / "project"
        ws.mkdir()
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
        assert _is_local_venv(ws) is False

    def test_python_in_different_workspace(self, tmp_path, monkeypatch):
        ws_a = tmp_path / "project_a"
        ws_a.mkdir()
        ws_b = tmp_path / "project_b"
        monkeypatch.setattr(sys, "executable", str(ws_b / "_tools/venv/bin/python"))
        assert _is_local_venv(ws_a) is False

    def test_symlink_venv_python(self, tmp_path, monkeypatch):
        """Venv python symlinked to uv-managed python must still return True."""
        ws = tmp_path / "project"
        venv_bin = ws / "_tools" / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        uv_python = ws / "_tools" / "python" / "cpython-3.14" / "python"
        uv_python.parent.mkdir(parents=True)
        uv_python.write_text("fake")

        venv_py = venv_bin / "python"
        try:
            venv_py.symlink_to(uv_python)
        except OSError:
            pytest.skip("symlink creation requires elevated privileges")

        monkeypatch.setattr(sys, "executable", str(venv_py))
        assert _is_local_venv(ws) is True


# ── derive_project_root ──────────────────────────────────────────────────


class TestDeriveProjectRoot:
    """Test root detection logic (canonical Python implementation).

    bootstrap.sh and bootstrap.ps1 mirror this logic — see
    ``derive_project_root()`` in ``_bootstrap.py``.
    """

    def test_submodule_standard_path(self, tmp_path):
        """Submodule at tools/framework -> superproject root."""
        fw = _git_init(tmp_path / "fw_repo")
        parent = _git_init(tmp_path / "parent")
        r = _git("submodule", "add", str(fw), "tools/framework", cwd=parent)
        assert r.returncode == 0, r.stderr
        _git("commit", "-m", "add submodule", cwd=parent)

        root = derive_project_root(parent / "tools" / "framework")

        assert _norm(root) == _norm(parent)

    def test_submodule_nonstandard_path(self, tmp_path):
        """Submodule at lib/repokit -> superproject root."""
        fw = _git_init(tmp_path / "fw_repo")
        parent = _git_init(tmp_path / "parent")
        r = _git("submodule", "add", str(fw), "lib/repokit", cwd=parent)
        assert r.returncode == 0, r.stderr
        _git("commit", "-m", "add submodule", cwd=parent)

        root = derive_project_root(parent / "lib" / "repokit")

        assert _norm(root) == _norm(parent)

    def test_submodule_deeply_nested(self, tmp_path):
        """Submodule at a/b/c/framework -> superproject root."""
        fw = _git_init(tmp_path / "fw_repo")
        parent = _git_init(tmp_path / "parent")
        r = _git("submodule", "add", str(fw), "a/b/c/framework", cwd=parent)
        assert r.returncode == 0, r.stderr
        _git("commit", "-m", "add submodule", cwd=parent)

        root = derive_project_root(parent / "a" / "b" / "c" / "framework")

        assert _norm(root) == _norm(parent)

    def test_nested_dir_in_repo(self, tmp_path):
        """Framework is a plain directory in a repo -> repo root.

        Monorepo case: toplevel != framework_dir.
        """
        repo = _git_init(tmp_path / "monorepo")
        fw_dir = repo / "packages" / "repokit"
        fw_dir.mkdir(parents=True)
        (fw_dir / "dummy").write_text("")
        _git("add", ".", cwd=repo)
        _git("commit", "-m", "add package", cwd=repo)

        root = derive_project_root(fw_dir)

        assert _norm(root) == _norm(repo)

    def test_not_a_git_repo(self, tmp_path):
        """Directory outside any git repo -> RuntimeError."""
        isolated = tmp_path / "not_a_repo"
        isolated.mkdir()

        with pytest.raises(RuntimeError, match="(?i)not a git repository"):
            derive_project_root(isolated)

    def test_standalone_clone_errors(self, tmp_path):
        """Framework is its own repo root, not a submodule -> RuntimeError.

        Dev symlink case: the framework dir IS the git toplevel, but
        there's no superproject. User must pass explicit root.
        """
        fw = _git_init(tmp_path / "standalone")

        with pytest.raises(RuntimeError, match="Could not determine project root"):
            derive_project_root(fw)
