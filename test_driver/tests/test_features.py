"""Tests for repo_tools.features (find_executable / require_executable)."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from repo_tools.features import find_executable, require_executable


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the find_executable cache between tests."""
    find_executable.cache_clear()
    yield
    find_executable.cache_clear()


class TestFindExecutable:
    def test_finds_in_venv_scripts(self, tmp_path):
        suffix = ".exe" if sys.platform == "win32" else ""
        fake_exe = tmp_path / f"fakecmd{suffix}"
        fake_exe.write_text("fake")
        fake_python = tmp_path / f"python{suffix}"
        fake_python.write_text("fake")

        with patch("repo_tools.features.sys") as mock_sys:
            mock_sys.executable = str(fake_python)
            mock_sys.platform = sys.platform
            result = find_executable("fakecmd")

        assert result == str(fake_exe)

    def test_falls_back_to_path(self, tmp_path):
        suffix = ".exe" if sys.platform == "win32" else ""
        fake_python = tmp_path / f"python{suffix}"
        fake_python.write_text("fake")

        with (
            patch("repo_tools.features.sys") as mock_sys,
            patch("repo_tools.features.shutil.which", return_value="/usr/bin/git"),
        ):
            mock_sys.executable = str(fake_python)
            mock_sys.platform = sys.platform
            result = find_executable("git")

        assert result == "/usr/bin/git"

    def test_returns_none_when_missing(self, tmp_path):
        suffix = ".exe" if sys.platform == "win32" else ""
        fake_python = tmp_path / f"python{suffix}"
        fake_python.write_text("fake")

        with (
            patch("repo_tools.features.sys") as mock_sys,
            patch("repo_tools.features.shutil.which", return_value=None),
        ):
            mock_sys.executable = str(fake_python)
            mock_sys.platform = sys.platform
            result = find_executable("nonexistent")

        assert result is None


class TestRequireExecutable:
    def test_returns_path_when_found(self):
        with patch("repo_tools.features.find_executable", return_value="/bin/ruff"):
            result = require_executable("ruff", feature="python")
        assert result == "/bin/ruff"

    def test_exits_with_helpful_error_when_missing(self, capture_logs):
        buf = capture_logs
        with (
            patch("repo_tools.features.find_executable", return_value=None),
            pytest.raises(SystemExit) as exc_info,
        ):
            require_executable("clang-format", feature="cpp")

        assert exc_info.value.code == 1
        output = buf.getvalue()
        assert "clang-format" in output
        assert "cpp" in output
        assert "repo.features" in output

    def test_error_mentions_feature_name(self, capture_logs):
        buf = capture_logs
        with (
            patch("repo_tools.features.find_executable", return_value=None),
            pytest.raises(SystemExit),
        ):
            require_executable("ruff", feature="python")

        assert "python" in buf.getvalue()
