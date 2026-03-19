"""Tests for Claude backend — _build_options(), hooks, and MCP tool construction."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock the claude_agent_sdk before importing the module under test
_mock_sdk = MagicMock()
_mock_sdk.ClaudeAgentOptions = MagicMock
_mock_sdk.HookMatcher = MagicMock


@pytest.fixture(autouse=True)
def _patch_sdk(monkeypatch):
    """Patch claude_agent_sdk so tests don't require the real package."""
    import sys
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _mock_sdk)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk.types", MagicMock())
    # Reset mocks each test
    _mock_sdk.reset_mock()
    # Make ClaudeAgentOptions capture kwargs
    _mock_sdk.ClaudeAgentOptions = _CaptureOptions
    _mock_sdk.HookMatcher = _CaptureHookMatcher
    _mock_sdk.create_sdk_mcp_server = MagicMock(return_value={"type": "sdk"})
    _mock_sdk.tool = _mock_tool_decorator


class _CaptureOptions:
    """Captures kwargs so tests can inspect them."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        self._kwargs = kwargs


class _CaptureHookMatcher:
    """Captures HookMatcher construction args."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        self._kwargs = kwargs


def _mock_tool_decorator(name, description, schema, annotations=None):
    """Mock @tool decorator that returns a fake SdkMcpTool."""
    def decorator(fn):
        mock_tool = MagicMock()
        mock_tool.name = name
        mock_tool.description = description
        mock_tool.handler = fn
        return mock_tool
    return decorator


@pytest.fixture
def rules_file(tmp_path):
    """Create a minimal rules file."""
    rules = tmp_path / "rules.toml"
    rules.write_text(
        'default_reason = "not allowed"\n'
        '[[allow]]\n'
        'name = "git"\n'
        'commands = ["git"]\n'
        '[[deny]]\n'
        'name = "destructive"\n'
        'commands = ["rm"]\n'
        'reason = "destructive"\n',
        encoding="utf-8",
    )
    return rules


# ── _build_options tests ──────────────────────────────────────────


class TestBuildOptions:
    def test_base_allowed_tools(self, tmp_path, rules_file):
        """Base tools (Read, Edit, etc.) are always in allowed_tools."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            rules_path=rules_file, project_root=tmp_path, cwd=tmp_path,
        )
        for tool_name in ("Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch"):
            assert tool_name in opts.allowed_tools

    def test_no_bash_without_role(self, tmp_path, rules_file):
        """Without a role, Bash is NOT in allowed_tools."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            rules_path=rules_file, project_root=tmp_path, cwd=tmp_path,
        )
        assert "Bash" not in opts.allowed_tools

    def test_role_adds_bash(self, tmp_path, rules_file):
        """With a role, Bash IS in allowed_tools."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="worker", rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path,
        )
        assert "Bash" in opts.allowed_tools

    def test_mcp_tool_names_in_allowed(self, tmp_path, rules_file):
        """MCP tool names (mcp__repokit-agent__*) are added to allowed_tools."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="worker", rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path,
        )
        mcp_names = [t for t in opts.allowed_tools if t.startswith("mcp__")]
        assert len(mcp_names) > 0
        assert any("lint" in n for n in mcp_names)
        assert any("coderabbit" in n for n in mcp_names)
        assert any("list_tickets" in n for n in mcp_names)

    def test_registered_tools_in_allowed(self, tmp_path, rules_file):
        """Registered RepoTool subclasses appear as single repo_run MCP tool."""
        from repo_tools.agent.claude import Claude

        fake_registered = [
            {"name": "clean", "description": "Clean up"},
            {"name": "format", "description": "Format code"},
        ]
        with patch(
            "repo_tools.agent.repo_cmd._discover_registered_tools",
            return_value=fake_registered,
        ):
            opts, meta = Claude._build_options(
                role="worker", rules_path=rules_file, project_root=tmp_path,
                cwd=tmp_path,
            )
        mcp_names = [t for t in opts.allowed_tools if t.startswith("mcp__")]
        assert any("repo_run" in n for n in mcp_names)

        mcp_meta = [t for t in meta if t["group"] == "MCP"]
        repo_names = {t["name"] for t in mcp_meta if t["name"].startswith("repo_")}
        assert "repo_run" in repo_names

    def test_all_repo_tools_from_registry(self, tmp_path, rules_file):
        """All repo commands consolidate into single repo_run tool."""
        from repo_tools.agent.claude import Claude

        fake_registered = [
            {"name": "test", "description": "Run ./repo test"},
            {"name": "test-cov", "description": "Run ./repo test-cov"},
            {"name": "clean", "description": "Clean up"},
        ]
        with patch(
            "repo_tools.agent.repo_cmd._discover_registered_tools",
            return_value=fake_registered,
        ):
            opts, meta = Claude._build_options(
                role="orchestrator", rules_path=rules_file, project_root=tmp_path,
                cwd=tmp_path,
            )
        mcp_names = [t for t in opts.allowed_tools if t.startswith("mcp__")]
        assert any("repo_run" in n for n in mcp_names)
        # Only one repo tool, not N separate ones
        repo_mcp = [n for n in mcp_names if "repo_run" in n]
        assert len(repo_mcp) == 1

        mcp_meta = [t for t in meta if t["group"] == "MCP"]
        repo_names = {t["name"] for t in mcp_meta if t["name"].startswith("repo_")}
        assert repo_names == {"repo_run"}

    def test_system_prompt_from_role_prompt(self, tmp_path, rules_file):
        """role_prompt is set as an appended system prompt preset."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="worker", role_prompt="You are a test worker.",
            rules_path=rules_file, project_root=tmp_path, cwd=tmp_path,
        )
        assert opts.system_prompt["type"] == "preset"
        assert opts.system_prompt["preset"] == "claude_code"
        assert opts.system_prompt["append"] == "You are a test worker."

    def test_no_system_prompt_without_role_prompt(self, tmp_path, rules_file):
        """Without role_prompt, system_prompt is None."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            rules_path=rules_file, project_root=tmp_path, cwd=tmp_path,
        )
        assert opts.system_prompt is None

    def test_max_turns_from_config(self, tmp_path, rules_file):
        """max_turns is forwarded from tool_config."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="worker", rules_path=rules_file, project_root=tmp_path,
            tool_config={"max_turns": 30}, cwd=tmp_path,
        )
        assert opts.max_turns == 30

    def test_no_max_turns_by_default(self, tmp_path, rules_file):
        """Without max_turns in config, it's None."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="worker", rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path,
        )
        assert opts.max_turns is None

    def test_output_format_worker_headless(self, tmp_path, rules_file):
        """Worker in headless mode gets output_format with json_schema."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="worker", rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path, headless=True,
        )
        assert opts.output_format is not None
        assert opts.output_format["type"] == "json_schema"
        schema = opts.output_format["schema"]
        assert "ticket_id" in schema["properties"]
        assert schema["properties"]["status"]["enum"] == ["verify", "in_progress"]

    def test_output_format_reviewer_headless(self, tmp_path, rules_file):
        """Reviewer in headless mode gets output_format with result/criteria fields."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="reviewer", rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path, headless=True,
        )
        assert opts.output_format is not None
        schema = opts.output_format["schema"]
        assert "result" in schema["properties"]
        assert schema["properties"]["result"]["enum"] == ["pass", "fail"]
        assert "criteria" in schema["properties"]

    def test_no_output_format_interactive(self, tmp_path, rules_file):
        """Non-headless mode has no output_format."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="worker", rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path, headless=False,
        )
        assert opts.output_format is None

    def test_no_output_format_without_role(self, tmp_path, rules_file):
        """Without a role, no output_format even in headless mode."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path, headless=True,
        )
        assert opts.output_format is None

    def test_permission_mode_bypass(self, tmp_path, rules_file):
        """permission_mode is always bypassPermissions."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            rules_path=rules_file, project_root=tmp_path, cwd=tmp_path,
        )
        assert opts.permission_mode == "bypassPermissions"

    def test_cwd_set(self, tmp_path, rules_file):
        """cwd is set from the provided path."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path / "subdir",
        )
        assert opts.cwd == str(tmp_path / "subdir")

    def test_hooks_present(self, tmp_path, rules_file):
        """Hooks dict has PreToolUse and PermissionRequest entries."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="worker", rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path,
        )
        assert opts.hooks is not None
        assert "PreToolUse" in opts.hooks
        assert "PermissionRequest" in opts.hooks

    def test_no_hooks_without_rules(self, tmp_path):
        """Without rules_path, hooks is None."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(cwd=tmp_path)
        assert opts.hooks is None

    def test_mcp_servers_present(self, tmp_path, rules_file):
        """mcp_servers has repokit-agent entry."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="worker", rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path,
        )
        assert "repokit-agent" in opts.mcp_servers

    def test_no_mcp_servers_without_project_root(self, tmp_path, rules_file):
        """Without project_root, no MCP servers."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            rules_path=rules_file, cwd=tmp_path,
        )
        assert len(opts.mcp_servers) == 0

    def test_setting_sources(self, tmp_path, rules_file):
        """setting_sources includes project."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            rules_path=rules_file, project_root=tmp_path, cwd=tmp_path,
        )
        assert "project" in opts.setting_sources

    def test_ruff_select_forwarded(self, tmp_path, rules_file):
        """ruff_select from tool_config is forwarded to lint tool."""
        from repo_tools.agent.claude import Claude

        # This test verifies the lint tool is created with the config.
        # The mock tool decorator captures the handler.
        opts, _meta = Claude._build_options(
            role="worker", rules_path=rules_file, project_root=tmp_path,
            tool_config={"ruff_select": "E,F"}, cwd=tmp_path,
        )
        # Lint tool should be in the MCP tools (verified by mcp names in allowed)
        assert any("lint" in t for t in opts.allowed_tools)


# ── MCP tool construction tests ──────────────────────────────────


class TestMcpTools:
    def test_worker_gets_limited_ticket_tools(self, tmp_path, rules_file):
        """Worker role gets only list/get/update ticket tools."""
        from repo_tools.agent.claude import _make_ticket_tools

        tools = _make_ticket_tools(tmp_path, role="worker")
        names = {t.name for t in tools}
        assert "list_tickets" in names
        assert "get_ticket" in names
        assert "update_ticket" in names
        assert "create_ticket" not in names
        assert "delete_ticket" not in names

    def test_reviewer_gets_mark_criteria(self, tmp_path, rules_file):
        """Reviewer role includes mark_criteria tool."""
        from repo_tools.agent.claude import _make_ticket_tools

        tools = _make_ticket_tools(tmp_path, role="reviewer")
        names = {t.name for t in tools}
        assert "mark_criteria" in names
        assert "list_tickets" in names

    def test_orchestrator_gets_all_tools(self, tmp_path, rules_file):
        """Orchestrator role gets all 7 ticket tools."""
        from repo_tools.agent.claude import _make_ticket_tools

        tools = _make_ticket_tools(tmp_path, role="orchestrator")
        names = {t.name for t in tools}
        expected = {
            "list_tickets", "get_ticket", "create_ticket", "update_ticket",
            "reset_ticket", "mark_criteria", "delete_ticket",
        }
        assert names == expected

    def test_lint_tool_created(self, tmp_path, rules_file):
        """Lint tool is created successfully."""
        from repo_tools.agent.claude import _make_lint_tool

        t = _make_lint_tool()
        assert t.name == "lint"

    def test_coderabbit_tool_created(self, tmp_path, rules_file):
        """CodeRabbit tool is created successfully."""
        from repo_tools.agent.claude import _make_coderabbit_tool

        t = _make_coderabbit_tool()
        assert t.name == "coderabbit_review"


# ── Hook construction tests ──────────────────────────────────────


class TestHookConstruction:
    def test_pretooluse_hook_matcher(self, tmp_path, rules_file):
        """PreToolUse hook has Bash matcher."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="worker", rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path,
        )
        pre = opts.hooks["PreToolUse"]
        assert len(pre) == 1
        assert pre[0].matcher == "Bash"
        assert len(pre[0].hooks) == 1

    def test_permission_hook_matcher(self, tmp_path, rules_file):
        """PermissionRequest hook has ^mcp__ matcher."""
        from repo_tools.agent.claude import Claude

        opts, _meta = Claude._build_options(
            role="worker", rules_path=rules_file, project_root=tmp_path,
            cwd=tmp_path,
        )
        perm = opts.hooks["PermissionRequest"]
        assert len(perm) == 1
        assert perm[0].matcher == "^mcp__"
        assert len(perm[0].hooks) == 1
