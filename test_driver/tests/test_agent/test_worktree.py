"""Tests for repo_tools.agent.worktree — bootstrap and lifecycle."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from repo_tools.agent.worktree import (
    _bootstrap_worktree,
    ensure_worktree,
    remove_worktree,
)


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo for worktree tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), check=True, capture_output=True,
    )
    # Need at least one commit for worktrees to work.
    (repo / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo), check=True, capture_output=True,
    )
    return repo


# ── _bootstrap_worktree ──────────────────────────────────────────


class TestBootstrapWorktree:
    @patch("repo_tools._bootstrap.write_shims")
    def test_generates_shims(self, mock_write_shims, tmp_path):
        """Bootstrap calls write_shims with the worktree as workspace_root."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        _bootstrap_worktree(wt)
        mock_write_shims.assert_called_once()
        kwargs = mock_write_shims.call_args[1]
        assert kwargs["workspace_root"] == wt

    @patch("repo_tools._bootstrap.write_shims")
    def test_uses_framework_root(self, mock_write_shims, tmp_path):
        """Bootstrap passes the framework root (not the worktree) as framework_root."""
        from repo_tools.core import _FRAMEWORK_ROOT

        wt = tmp_path / "worktree"
        wt.mkdir()
        _bootstrap_worktree(wt)
        kwargs = mock_write_shims.call_args[1]
        assert kwargs["framework_root"] == _FRAMEWORK_ROOT

    @patch("repo_tools._bootstrap.write_shims", side_effect=OSError("disk full"))
    def test_propagates_exceptions(self, mock_write_shims, tmp_path):
        """Bootstrap does NOT swallow errors — exceptions propagate."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        with pytest.raises(OSError, match="disk full"):
            _bootstrap_worktree(wt)


# ── ensure_worktree ──────────────────────────────────────────────


class TestEnsureWorktree:
    @patch("repo_tools.agent.worktree._bootstrap_worktree")
    def test_creates_worktree_and_bootstraps(self, mock_bootstrap, git_repo):
        """New worktree is created and bootstrap is called."""
        wt = ensure_worktree(git_repo, "test-ticket")
        expected = git_repo / "_agent" / "worktrees" / "test-ticket"
        assert wt == expected
        assert wt.exists()
        mock_bootstrap.assert_called_once_with(expected)

    @patch("repo_tools.agent.worktree._bootstrap_worktree")
    def test_reuse_existing_skips_bootstrap(self, mock_bootstrap, git_repo):
        """Reusing an existing worktree does NOT re-bootstrap."""
        # Create the worktree first time.
        wt = ensure_worktree(git_repo, "test-ticket")
        mock_bootstrap.reset_mock()

        # Second call should reuse.
        wt2 = ensure_worktree(git_repo, "test-ticket")
        assert wt2 == wt
        mock_bootstrap.assert_not_called()

    @patch("repo_tools.agent.worktree._bootstrap_worktree")
    def test_worktree_branch_name(self, mock_bootstrap, git_repo):
        """Worktree branch is named worktree-<ticket>."""
        ensure_worktree(git_repo, "my-fix")
        result = subprocess.run(
            ["git", "branch", "--list", "worktree-my-fix"],
            cwd=str(git_repo), capture_output=True, text=True,
        )
        assert "worktree-my-fix" in result.stdout

    @patch("repo_tools.agent.worktree._bootstrap_worktree",
           side_effect=OSError("write_shims failed"))
    def test_bootstrap_failure_propagates(self, mock_bootstrap, git_repo):
        """If bootstrap fails, ensure_worktree raises — dispatch aborts early."""
        with pytest.raises(OSError, match="write_shims failed"):
            ensure_worktree(git_repo, "bad-ticket")


# ── remove_worktree ─────────────────────────────────────────────


class TestRemoveWorktree:
    @patch("repo_tools.agent.worktree._bootstrap_worktree")
    def test_remove_existing(self, mock_bootstrap, git_repo):
        """Removing an existing worktree cleans up the directory."""
        ensure_worktree(git_repo, "to-remove")
        wt = git_repo / "_agent" / "worktrees" / "to-remove"
        assert wt.exists()

        remove_worktree(git_repo, "to-remove")
        assert not wt.exists()

    def test_remove_nonexistent_prunes(self, git_repo):
        """Removing a nonexistent worktree just prunes stale entries."""
        # Should not raise.
        remove_worktree(git_repo, "never-existed")


# ── Prompt templates ─────────────────────────────────────────────


class TestPromptTemplates:
    """Verify prompt templates don't use removed placeholders."""

    @pytest.fixture
    def prompts_dir(self):
        return Path(__file__).resolve().parents[3] / "repo_tools" / "agent" / "prompts"

    @pytest.mark.parametrize("template", ["common.txt", "worker.txt", "reviewer.txt", "orchestrator.txt"])
    def test_no_repo_cmd_placeholder(self, prompts_dir, template):
        """Templates must not contain {repo_cmd} — agents use MCP tools."""
        content = (prompts_dir / template).read_text(encoding="utf-8")
        assert "{repo_cmd}" not in content

    @pytest.mark.parametrize("template", ["common.txt", "worker.txt", "reviewer.txt", "orchestrator.txt"])
    def test_no_framework_root_placeholder(self, prompts_dir, template):
        """Templates must not contain {framework_root}."""
        content = (prompts_dir / template).read_text(encoding="utf-8")
        assert "{framework_root}" not in content

    def test_worker_references_mcp_tools(self, prompts_dir):
        """Worker prompt should reference repo_run MCP tool."""
        content = (prompts_dir / "worker.txt").read_text(encoding="utf-8")
        assert "repo_run" in content

    def test_reviewer_references_mcp_tools(self, prompts_dir):
        """Reviewer prompt should reference repo_run MCP tool."""
        content = (prompts_dir / "reviewer.txt").read_text(encoding="utf-8")
        assert "repo_run" in content

    def test_common_references_mcp_tools(self, prompts_dir):
        """Common prompt should list repo_run MCP tool."""
        content = (prompts_dir / "common.txt").read_text(encoding="utf-8")
        assert "repo_run" in content


# ── _render_role_prompt (no {repo_cmd}) ──────────────────────────


class TestRenderRolePrompt:
    """Verify _render_role_prompt works without repo_cmd/framework_root kwargs."""

    def test_worker_renders_without_repo_cmd(self):
        from repo_tools.agent.tool import _render_role_prompt

        result = _render_role_prompt(
            "worker",
            ticket_id="test-1",
            ticket_path="/tmp/test-1.json",
            project_root="/tmp/project",
        )
        assert "test-1" in result
        assert "repo_run" in result

    def test_reviewer_renders_without_repo_cmd(self):
        from repo_tools.agent.tool import _render_role_prompt

        result = _render_role_prompt(
            "reviewer",
            ticket_id="test-1",
            ticket_path="/tmp/test-1.json",
            project_root="/tmp/project",
        )
        assert "test-1" in result
        assert "repo_run" in result

    def test_orchestrator_renders_without_kwargs(self):
        from repo_tools.agent.tool import _render_role_prompt

        result = _render_role_prompt("orchestrator")
        assert "repo_run" in result
