"""Tests for AgentTool click.Command structure."""

from __future__ import annotations

import click
from click.testing import CliRunner

from repo_tools.agent.tool import AgentTool
from repo_tools.cli import _make_tool_command


def _build_agent_cmd() -> click.BaseCommand:
    return _make_tool_command(AgentTool(), {})


def _wrap_agent_command():
    """Wrap agent command in a parent CLI with mock context."""
    cmd = _build_agent_cmd()

    @click.group()
    @click.pass_context
    def cli(ctx):
        ctx.ensure_object(dict)
        ctx.obj["workspace_root"] = "/tmp"
        ctx.obj["config"] = {}
        ctx.obj["tokens"] = {}
        ctx.obj["dimensions"] = {}

    cli.add_command(cmd)
    return cli


class TestAgentHelp:
    def test_agent_help_shows_options(self):
        runner = CliRunner()
        cli = _wrap_agent_command()
        result = runner.invoke(cli, ["agent", "--help"])
        assert result.exit_code == 0
        assert "--role" in result.output
        assert "--ticket" in result.output
        assert "--backend" in result.output
        assert "--max-turns" in result.output
        assert "--debug-hooks" in result.output

    def test_agent_is_a_group_with_ticket_subcommand(self):
        """agent is a group with a ticket subcommand."""
        cmd = _build_agent_cmd()
        assert isinstance(cmd, click.Group)
        assert "ticket" in cmd.commands

    def test_agent_is_a_group_with_worktree_subcommand(self):
        cmd = _build_agent_cmd()
        assert isinstance(cmd, click.Group)
        assert "worktree" in cmd.commands

    def test_role_without_ticket_errors(self):
        runner = CliRunner()
        cli = _wrap_agent_command()
        result = runner.invoke(cli, ["agent", "--role", "worker"])
        assert result.exit_code != 0
        assert "--role and --ticket must be used together" in result.output

    def test_ticket_without_role_errors(self):
        runner = CliRunner()
        cli = _wrap_agent_command()
        result = runner.invoke(cli, ["agent", "--ticket", "G1_1"])
        assert result.exit_code != 0
        assert "--role and --ticket must be used together" in result.output


class TestDefaultArgs:
    def test_defaults(self):
        tool = AgentTool()
        defaults = tool.default_args({})
        assert defaults["backend"] == "cli"
        assert defaults["debug_hooks"] is False
        assert defaults["max_turns"] is None
        assert defaults["role"] is None
        assert defaults["ticket"] is None
