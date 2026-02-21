"""Tests for AgentTool click.Group structure."""

from __future__ import annotations

import click
from click.testing import CliRunner

from repo_tools.agent.tool import _make_agent_group


def _wrap_agent_group():
    """Wrap agent group in a parent CLI with mock context."""
    group = _make_agent_group()

    @click.group()
    @click.pass_context
    def cli(ctx):
        ctx.ensure_object(dict)
        ctx.obj["workspace_root"] = "/tmp"
        ctx.obj["config"] = {}
        ctx.obj["tokens"] = {}
        ctx.obj["dimensions"] = {}

    cli.add_command(group)
    return cli


class TestAgentHelp:
    def test_agent_help(self):
        runner = CliRunner()
        cli = _wrap_agent_group()
        result = runner.invoke(cli, ["agent", "--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "team" in result.output
        # send is gone — replaced by MCP send_message tool
        assert "send" not in result.output

    def test_team_help(self):
        runner = CliRunner()
        cli = _wrap_agent_group()
        result = runner.invoke(cli, ["agent", "team", "--help"])
        assert result.exit_code == 0
        assert "new" in result.output
        assert "attach" in result.output
        assert "status" in result.output
        # kill is gone — Ctrl+C on the blocking team new/attach handles teardown
        assert "kill" not in result.output
