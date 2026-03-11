"""Tests for InitTool (repo init) and _bootstrap helpers."""

from __future__ import annotations

import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from repo_tools._bootstrap import (
    collect_feature_groups,
    find_uv,
    load_framework_pyproject,
    write_pyproject,
    write_shims,
)
from repo_tools.core import RepoTool, _TOOL_REGISTRY, registered_tool_deps
from repo_tools.init import InitTool, _CONFIG_TEMPLATE, _CI_TEMPLATE, _CLAUDE_TEMPLATE

_FRAMEWORK_TOML = textwrap.dedent("""\
    [project]
    name = "repokit"
    version = "0.3.0"
    requires-python = ">=3.11"
    dependencies = [
        "click>=8.0",
        "pyyaml>=6.0",
    ]

    [dependency-groups]
    cpp = ["clang-format>=19.0", "clang-tidy>=19.0"]
    python = ["ruff>=0.4"]

    [tool.uv]
    package = false
""")


@pytest.fixture
def fw_root(tmp_path):
    """Framework root with a pyproject.toml."""
    root = tmp_path / "framework"
    root.mkdir()
    (root / "pyproject.toml").write_text(_FRAMEWORK_TOML)
    return root


@pytest.fixture
def init_ctx(make_tool_context, tmp_path, fw_root):
    """ToolContext wired to a temp framework and workspace."""
    ws = tmp_path / "project"
    ws.mkdir()
    (fw_root / "_managed").mkdir(parents=True, exist_ok=True)

    return make_tool_context(
        workspace_root=ws,
        tokens_override={"framework_root": str(fw_root)},
    )


@pytest.fixture
def init_ctx_with_features(make_tool_context, tmp_path, fw_root):
    """ToolContext with repo.features configured."""
    ws = tmp_path / "project"
    ws.mkdir()
    (fw_root / "_managed").mkdir(parents=True, exist_ok=True)

    return make_tool_context(
        config={"repo": {"features": ["python"]}},
        workspace_root=ws,
        tokens_override={"framework_root": str(fw_root)},
    )


@pytest.fixture
def init_ctx_with_extra_deps(make_tool_context, tmp_path, fw_root):
    """ToolContext with repo.extra_deps configured."""
    ws = tmp_path / "project"
    ws.mkdir()
    (fw_root / "_managed").mkdir(parents=True, exist_ok=True)

    return make_tool_context(
        config={"repo": {"extra_deps": ["somelib>=1.0", "otherlib>=2.0"]}},
        workspace_root=ws,
        tokens_override={"framework_root": str(fw_root)},
    )


# ── find_uv ─────────────────────────────────────────────────────────────────


class TestFindUv:
    def test_finds_uv_in_managed_bin(self, tmp_path):
        managed = tmp_path / "framework" / "_managed"
        managed_bin = managed / "bin"
        managed_bin.mkdir(parents=True)
        suffix = ".exe" if sys.platform == "win32" else ""
        uv = managed_bin / f"uv{suffix}"
        uv.write_text("fake")

        assert find_uv(managed) == str(uv)

    @patch("repo_tools._bootstrap.shutil.which", return_value="/usr/bin/uv")
    def test_falls_back_to_path(self, _which, tmp_path):
        managed = tmp_path / "framework" / "_managed"
        managed.mkdir(parents=True)
        assert find_uv(managed) == "/usr/bin/uv"

    @patch("repo_tools._bootstrap.shutil.which", return_value=None)
    def test_returns_none_when_missing(self, _which, tmp_path):
        managed = tmp_path / "framework" / "_managed"
        managed.mkdir(parents=True)
        assert find_uv(managed) is None


# ── pyproject helpers ────────────────────────────────────────────────────────


class TestLoadFrameworkPyproject:
    def test_loads_valid_toml(self, fw_root):
        data = load_framework_pyproject(fw_root)
        assert "click>=8.0" in data["project"]["dependencies"]

    def test_exits_when_missing(self, tmp_path):
        with pytest.raises(SystemExit):
            load_framework_pyproject(tmp_path / "nodir")


class TestCollectFeatureGroups:
    def test_all_groups_when_no_features(self):
        fw = {"dependency-groups": {"cpp": ["clang-format>=19.0"], "python": ["ruff>=0.4"]}}
        result = collect_feature_groups(fw, [])
        assert "cpp" in result
        assert "python" in result

    def test_selects_requested_features(self):
        fw = {"dependency-groups": {"cpp": ["clang-format>=19.0"], "python": ["ruff>=0.4"]}}
        result = collect_feature_groups(fw, ["python"])
        assert "python" in result
        assert "cpp" not in result

    def test_warns_unknown_feature(self, capsys):
        fw = {"dependency-groups": {"python": ["ruff>=0.4"]}}
        collect_feature_groups(fw, ["nonexistent"])
        assert "nonexistent" in capsys.readouterr().err


class TestRegisteredToolDeps:
    def test_empty_when_no_tools_have_deps(self):
        saved = dict(_TOOL_REGISTRY)
        try:
            _TOOL_REGISTRY.clear()

            class NoDeps(RepoTool):
                name = "_test_nodeps"
                deps: list[str] = []

            _TOOL_REGISTRY["_test_nodeps"] = NoDeps()
            assert registered_tool_deps() == []
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)

    def test_collects_and_deduplicates(self):
        saved = dict(_TOOL_REGISTRY)
        try:
            _TOOL_REGISTRY.clear()

            class ToolA(RepoTool):
                name = "_test_a"
                deps = ["requests>=2.0", "click>=8.0"]

            class ToolB(RepoTool):
                name = "_test_b"
                deps = ["click>=8.0", "boto3>=1.0"]

            _TOOL_REGISTRY["_test_a"] = ToolA()
            _TOOL_REGISTRY["_test_b"] = ToolB()
            result = registered_tool_deps()
            assert "click>=8.0" in result
            assert result.count("click>=8.0") == 1
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)

    def test_returns_sorted(self):
        saved = dict(_TOOL_REGISTRY)
        try:
            _TOOL_REGISTRY.clear()

            class ToolZ(RepoTool):
                name = "_test_z"
                deps = ["zlib>=1.0", "aiohttp>=3.0"]

            _TOOL_REGISTRY["_test_z"] = ToolZ()
            result = registered_tool_deps()
            assert result == sorted(result)
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)


class TestWritePyproject:
    def test_writes_valid_structure(self, tmp_path):
        path = tmp_path / "tools" / "pyproject.toml"
        groups = {
            "core": ["click>=8.0"],
            "cpp": ["clang-format>=19.0"],
            "python": ["ruff>=0.4"],
            "tools": ["pytest>=7.0"],
        }
        write_pyproject(path, groups)
        text = path.read_text()
        assert '"click>=8.0"' in text
        assert "[dependency-groups]" in text
        assert '"clang-format>=19.0"' in text
        assert '"ruff>=0.4"' in text
        assert '"pytest>=7.0"' in text
        assert "tools = [" in text
        assert "default-groups" in text
        assert "package = false" in text

    def test_all_groups_in_default_groups(self, tmp_path):
        path = tmp_path / "tools" / "pyproject.toml"
        groups = {"core": ["click>=8.0"], "python": ["ruff>=0.4"]}
        write_pyproject(path, groups)
        text = path.read_text()
        assert '"core"' in text
        assert '"python"' in text

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "pyproject.toml"
        write_pyproject(path, {"core": ["click>=8.0"]})
        assert path.exists()


# ── InitTool.execute ─────────────────────────────────────────────────────────


class TestInitTool:
    @pytest.fixture(autouse=True)
    def _allow_init(self):
        with patch("repo_tools.init._is_local_venv", return_value=True):
            yield

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_generates_pyproject_and_syncs(self, _uv, mock_run, init_ctx, fw_root):
        mock_run.return_value = MagicMock(returncode=0)
        tool = InitTool()

        tool.execute(init_ctx, {})

        # Should have generated _managed/pyproject.toml
        pyproject = fw_root / "_managed" / "pyproject.toml"
        assert pyproject.exists()
        content = pyproject.read_text()
        assert '"click>=8.0"' in content
        assert '"pyyaml>=6.0"' in content
        assert "[dependency-groups]" in content
        assert '"ruff>=0.4"' in content

        # uv sync should have been called
        assert mock_run.call_count == 1
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "/bin/uv"
        assert "sync" in cmd

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_includes_only_selected_feature_groups(self, _uv, mock_run, init_ctx_with_features, fw_root):
        mock_run.return_value = MagicMock(returncode=0)
        tool = InitTool()

        tool.execute(init_ctx_with_features, {})

        pyproject = fw_root / "_managed" / "pyproject.toml"
        content = pyproject.read_text()
        assert '"ruff>=0.4"' in content
        assert "clang-format" not in content
        assert "clang-tidy" not in content

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_passes_tool_deps_to_bootstrap(self, _uv, mock_run, init_ctx, fw_root):
        mock_run.return_value = MagicMock(returncode=0)
        saved = dict(_TOOL_REGISTRY)
        try:
            class FakeTool(RepoTool):
                name = "_test_fake"
                deps = ["requests>=2.0"]

            _TOOL_REGISTRY["_test_fake"] = FakeTool()

            tool = InitTool()
            tool.execute(init_ctx, {})

            content = (fw_root / "_managed" / "pyproject.toml").read_text()
            assert '"requests>=2.0"' in content
            assert "tools = [" in content
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_merges_extra_deps_with_tool_deps(self, _uv, mock_run, init_ctx_with_extra_deps, fw_root):
        mock_run.return_value = MagicMock(returncode=0)
        saved = dict(_TOOL_REGISTRY)
        try:
            class FakeTool(RepoTool):
                name = "_test_fake"
                deps = ["requests>=2.0"]

            _TOOL_REGISTRY["_test_fake"] = FakeTool()

            tool = InitTool()
            tool.execute(init_ctx_with_extra_deps, {})

            content = (fw_root / "_managed" / "pyproject.toml").read_text()
            # extra_deps from config
            assert '"somelib>=1.0"' in content
            assert '"otherlib>=2.0"' in content
            # tool deps
            assert '"requests>=2.0"' in content
            assert "tools = [" in content
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_uv_sync_gets_project_environment(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=0)
        tool = InitTool()

        tool.execute(init_ctx, {})

        env = mock_run.call_args.kwargs.get("env", {})
        assert "UV_PROJECT_ENVIRONMENT" in env
        assert "_managed" in env["UV_PROJECT_ENVIRONMENT"]
        assert "venv" in env["UV_PROJECT_ENVIRONMENT"]

    @patch("repo_tools.init._is_local_venv", return_value=False)
    def test_refuses_foreign_workspace(self, _venv, make_tool_context, tmp_path):
        """init must refuse when sys.executable is not in workspace's venv."""
        ws = tmp_path / "foreign"
        ws.mkdir()
        ctx = make_tool_context(workspace_root=ws)

        tool = InitTool()
        with pytest.raises(SystemExit) as exc_info:
            tool.execute(ctx, {})
        assert exc_info.value.code == 1

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_exits_on_sync_failure(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=1)
        tool = InitTool()

        with pytest.raises(SystemExit) as exc_info:
            tool.execute(init_ctx, {})
        assert exc_info.value.code == 1

    @patch("repo_tools._bootstrap.find_uv", return_value=None)
    def test_exits_when_uv_missing(self, _uv, init_ctx):
        tool = InitTool()

        with pytest.raises(SystemExit) as exc_info:
            tool.execute(init_ctx, {})
        assert exc_info.value.code == 1

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_patches_gitignore(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=0)
        tool = InitTool()

        tool.execute(init_ctx, {})

        gitignore = init_ctx.workspace_root / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert "repo" in content
        assert "config.local.yaml" in content

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_clean_removes_pyproject_and_lock(self, _uv, mock_run, init_ctx, fw_root):
        mock_run.return_value = MagicMock(returncode=0)
        managed = fw_root / "_managed"
        managed.mkdir(parents=True, exist_ok=True)
        pyproject = managed / "pyproject.toml"
        pyproject.write_text("[invalid")
        lock = managed / "uv.lock"
        lock.write_text("stale")

        tool = InitTool()
        tool.execute(init_ctx, {"clean": True})

        # pyproject should be regenerated (not the corrupt one)
        assert pyproject.exists()
        content = pyproject.read_text()
        assert '"click>=8.0"' in content
        # lock should have been removed (uv sync regenerates it)
        assert not lock.exists()

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_clean_is_safe_when_nothing_exists(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=0)
        tool = InitTool()
        # Should not raise even if venv/pyproject don't exist
        tool.execute(init_ctx, {"clean": True})


# ── Config template generation ──────────────────────────────────────────────


class TestInitConfigTemplate:
    @pytest.fixture(autouse=True)
    def _allow_init(self):
        with patch("repo_tools.init._is_local_venv", return_value=True):
            yield

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_generates_config_when_no_config_exists(self, _uv, mock_run, init_ctx):
        """init writes config.yaml template when no config file exists."""
        mock_run.return_value = MagicMock(returncode=0)
        tool = InitTool()
        tool.execute(init_ctx, {})

        config_path = init_ctx.workspace_root / "config.yaml"
        assert config_path.exists()
        content = config_path.read_text(encoding="utf-8")
        assert content == _CONFIG_TEMPLATE
        assert "# repo:" in content

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_skips_when_repokit_config_exists(self, _uv, mock_run, init_ctx, capsys):
        """init skips template generation when repokit config already exists."""
        mock_run.return_value = MagicMock(returncode=0)
        # Pre-create config.yaml as the repokit config
        (init_ctx.workspace_root / "config.yaml").write_text("repo:\n  tokens: {}\n")

        tool = InitTool()
        tool.execute(init_ctx, {})

        output = capsys.readouterr().out
        assert "skipping template generation" in output
        # File should still have original content (not overwritten)
        content = (init_ctx.workspace_root / "config.yaml").read_text()
        assert content == "repo:\n  tokens: {}\n"

    @patch("repo_tools.init.click.prompt", return_value="repokit.yaml")
    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_prompts_for_alt_name_when_foreign_config_exists(
        self, _uv, mock_run, mock_prompt, init_ctx, fw_root,
    ):
        """init prompts for alt filename when foreign config.yaml exists."""
        mock_run.return_value = MagicMock(returncode=0)
        # Pre-create foreign config.yaml (not a repokit config)
        (init_ctx.workspace_root / "config.yaml").write_text("some: foreign_config\n")

        tool = InitTool()
        tool.execute(init_ctx, {})

        # Should have prompted the user
        mock_prompt.assert_called_once()

        # Template written to alt filename
        alt_path = init_ctx.workspace_root / "repokit.yaml"
        assert alt_path.exists()
        assert alt_path.read_text(encoding="utf-8") == _CONFIG_TEMPLATE

        # config_name persisted
        config_name_path = fw_root / "_managed" / "config_name"
        assert config_name_path.exists()
        assert config_name_path.read_text(encoding="utf-8") == "repokit.yaml"

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_skips_when_override_config_exists(
        self, _uv, mock_run, make_tool_context, tmp_path, fw_root, capsys,
    ):
        """init skips when the override config file already exists."""
        mock_run.return_value = MagicMock(returncode=0)
        ws = tmp_path / "project"
        ws.mkdir()
        (fw_root / "_managed").mkdir(parents=True, exist_ok=True)

        # Set override at the framework root that get_config_file() checks
        (fw_root / "_managed" / "config_name").write_text("repokit.yaml")
        (ws / "repokit.yaml").write_text("existing: config\n")

        ctx = make_tool_context(
            workspace_root=ws,
            tokens_override={"framework_root": str(fw_root)},
        )
        tool = InitTool()
        with patch("repo_tools.core._FRAMEWORK_ROOT", fw_root):
            tool.execute(ctx, {})

        output = capsys.readouterr().out
        assert "skipping template generation" in output


# ── write_shims ─────────────────────────────────────────────────────────────


class TestWriteShims:
    def test_generates_bash_shim(self, tmp_path):
        fw = tmp_path / "framework"
        fw.mkdir()
        ws = tmp_path / "project"
        ws.mkdir()

        write_shims(fw, ws)

        shim = ws / "repo"
        assert shim.exists()
        content = shim.read_text()
        assert content.startswith("#!/bin/bash\n")
        assert "repo_tools.cli" in content
        assert "--workspace-root" in content
        # bash shim must use forward slashes even on Windows
        assert "\\" not in content

    def test_bash_shim_uses_lf_newlines(self, tmp_path):
        fw = tmp_path / "framework"
        fw.mkdir()
        ws = tmp_path / "project"
        ws.mkdir()

        write_shims(fw, ws)

        raw = (ws / "repo").read_bytes()
        assert b"\r\n" not in raw

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_generates_cmd_shim_on_windows(self, tmp_path):
        fw = tmp_path / "framework"
        fw.mkdir()
        ws = tmp_path / "project"
        ws.mkdir()

        write_shims(fw, ws)

        cmd = ws / "repo.cmd"
        assert cmd.exists()
        content = cmd.read_text()
        assert "@echo off" in content
        assert "repo_tools.cli" in content
        assert "--workspace-root" in content

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only")
    def test_no_cmd_shim_on_unix(self, tmp_path):
        fw = tmp_path / "framework"
        fw.mkdir()
        ws = tmp_path / "project"
        ws.mkdir()

        write_shims(fw, ws)

        assert not (ws / "repo.cmd").exists()


# ── Framework at root rejected ───────────────────────────────────────────────


class TestFrameworkAtRootRejected:
    """framework_root.parent == workspace_root should be rejected."""

    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_rejects_framework_at_project_root(self, _uv, tmp_path):
        """run() exits with error when framework lives at the project root."""
        from repo_tools._bootstrap import run

        # framework_root = tmp_path/framework, so framework_root.parent = tmp_path
        # workspace_root = tmp_path  →  tools_dir == workspace_root
        fw = tmp_path / "framework"
        fw.mkdir()
        (fw / "pyproject.toml").write_text(_FRAMEWORK_TOML)

        with pytest.raises(SystemExit):
            run(fw, tmp_path)


# ── managed_dir paths correct ────────────────────────────────────────────────


class TestManagedDirPaths:
    """Verify find_uv, run, write_shims all use _managed/ paths."""

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_run_creates_managed_dir(self, _uv, mock_run, tmp_path):
        from repo_tools._bootstrap import run

        fw = tmp_path / "tools" / "framework"
        fw.mkdir(parents=True)
        (fw / "pyproject.toml").write_text(_FRAMEWORK_TOML)
        ws = tmp_path

        mock_run.return_value = MagicMock(returncode=0)
        run(fw, ws)

        managed = fw / "_managed"
        assert managed.is_dir()
        assert (managed / "pyproject.toml").exists()

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_run_passes_managed_venv_to_uv_sync(self, _uv, mock_run, tmp_path):
        from repo_tools._bootstrap import run

        fw = tmp_path / "tools" / "framework"
        fw.mkdir(parents=True)
        (fw / "pyproject.toml").write_text(_FRAMEWORK_TOML)
        ws = tmp_path

        mock_run.return_value = MagicMock(returncode=0)
        run(fw, ws)

        env = mock_run.call_args.kwargs.get("env", {})
        assert "_managed" in env["UV_PROJECT_ENVIRONMENT"]
        assert "venv" in env["UV_PROJECT_ENVIRONMENT"]


# ── CI template generation ──────────────────────────────────────────────────


class TestInitCITemplate:
    @pytest.fixture(autouse=True)
    def _allow_init(self):
        with patch("repo_tools.init._is_local_venv", return_value=True):
            yield

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_generates_ci_when_no_workflow_exists(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=0)
        tool = InitTool()
        tool.execute(init_ctx, {})

        ci_path = init_ctx.workspace_root / ".github" / "workflows" / "ci.yml"
        assert ci_path.exists()
        content = ci_path.read_text(encoding="utf-8")
        assert content == _CI_TEMPLATE

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_skips_ci_when_workflow_exists(self, _uv, mock_run, init_ctx, capsys):
        mock_run.return_value = MagicMock(returncode=0)
        # Pre-create the CI workflow file
        ci_dir = init_ctx.workspace_root / ".github" / "workflows"
        ci_dir.mkdir(parents=True, exist_ok=True)
        (ci_dir / "ci.yml").write_text("existing: content\n")

        tool = InitTool()
        tool.execute(init_ctx, {})

        # File should still have original content (not overwritten)
        content = (ci_dir / "ci.yml").read_text()
        assert content == "existing: content\n"
        # Should report skipping
        output = capsys.readouterr().out
        assert "skipping template generation" in output


class TestInitClaudeTemplate:
    @pytest.fixture(autouse=True)
    def _allow_init(self):
        with patch("repo_tools.init._is_local_venv", return_value=True):
            yield

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_generates_claude_md_when_absent(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=0)
        tool = InitTool()
        tool.execute(init_ctx, {})

        claude_path = init_ctx.workspace_root / "CLAUDE.md"
        assert claude_path.exists()
        content = claude_path.read_text(encoding="utf-8")
        assert "## Repo tooling" in content
        assert "./repo" in content

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_appends_to_existing_claude_md(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=0)
        claude_path = init_ctx.workspace_root / "CLAUDE.md"
        claude_path.write_text("# My Project\n\nCustom instructions.\n")

        tool = InitTool()
        tool.execute(init_ctx, {})

        content = claude_path.read_text(encoding="utf-8")
        assert content.startswith("# My Project\n")
        assert "Custom instructions." in content
        assert "## Repo tooling" in content

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_skips_when_section_present(self, _uv, mock_run, init_ctx, capsys):
        mock_run.return_value = MagicMock(returncode=0)
        claude_path = init_ctx.workspace_root / "CLAUDE.md"
        claude_path.write_text("## Repo tooling\n\nAlready here.\n")

        tool = InitTool()
        tool.execute(init_ctx, {})

        content = claude_path.read_text(encoding="utf-8")
        assert content == "## Repo tooling\n\nAlready here.\n"
        output = capsys.readouterr().out
        assert "skipping" in output

    @patch("repo_tools._bootstrap.subprocess.run")
    @patch("repo_tools._bootstrap.find_uv", return_value="/bin/uv")
    def test_uses_relative_framework_path(self, _uv, mock_run, init_ctx):
        mock_run.return_value = MagicMock(returncode=0)
        tool = InitTool()
        tool.execute(init_ctx, {})

        content = (init_ctx.workspace_root / "CLAUDE.md").read_text(encoding="utf-8")
        # fw_root is tmp_path/framework, ws is tmp_path/project → ../framework
        assert "../framework" in content
        assert "\\" not in content
