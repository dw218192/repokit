"""Tests for AgentTool click.Command structure."""

from __future__ import annotations

import click
from click.testing import CliRunner

from repo_tools.agent.tool import _make_agent_command


def _wrap_agent_command():
    """Wrap agent command in a parent CLI with mock context."""
    cmd = _make_agent_command()

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
        assert "--worktree" in result.output

    def test_agent_is_a_group_with_ticket_subcommand(self):
        """agent is a group with a ticket subcommand."""
        cmd = _make_agent_command()
        assert isinstance(cmd, click.Group)
        assert "ticket" in cmd.commands

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
