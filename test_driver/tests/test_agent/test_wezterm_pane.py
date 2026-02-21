"""Tests for PaneSession, _SendBuilder, and ensure_installed."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, call, patch

import pytest

from repo_tools.agent.wezterm import PaneSession, _SendBuilder, ensure_installed


def _mock_result(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ── ensure_installed ──────────────────────────────────────────────


class TestEnsureInstalled:
    @patch.dict(os.environ, {"WEZTERM_PANE": "1"})
    @patch("repo_tools.agent.wezterm.shutil.which", return_value="/usr/bin/wezterm")
    def test_returns_path(self, mock_which):
        assert ensure_installed() == "/usr/bin/wezterm"

    @patch.dict(os.environ, {"WEZTERM_PANE": "1"})
    @patch("repo_tools.agent.wezterm.shutil.which", return_value=None)
    def test_not_found_exits(self, mock_which):
        with pytest.raises(SystemExit):
            ensure_installed()

    @patch.dict(os.environ, {}, clear=True)
    @patch("repo_tools.agent.wezterm.shutil.which", return_value="/usr/bin/wezterm")
    def test_no_wezterm_pane_exits(self, mock_which):
        # Remove WEZTERM_PANE if it happens to be set
        os.environ.pop("WEZTERM_PANE", None)
        with pytest.raises(SystemExit):
            ensure_installed()


# ── PaneSession methods ───────────────────────────────────────────


class TestPaneSession:
    @patch("repo_tools.agent.wezterm._run_cli")
    def test_get_text(self, mock_cli):
        mock_cli.return_value = _mock_result(stdout="hello world\n")
        session = PaneSession(42)
        assert session.get_text() == "hello world\n"
        mock_cli.assert_called_once_with("get-text", "--pane-id", "42")

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_get_text_failure(self, mock_cli):
        mock_cli.return_value = _mock_result(returncode=1)
        session = PaneSession(42)
        assert session.get_text() == ""

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_send_keys(self, mock_cli):
        mock_cli.return_value = _mock_result()
        session = PaneSession(42)
        assert session.send_keys("\x1b") is True
        mock_cli.assert_called_once_with(
            "send-text", "--pane-id", "42", "--no-paste", input_text="\x1b",
        )

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_send_text(self, mock_cli):
        mock_cli.return_value = _mock_result()
        session = PaneSession(42)
        assert session.send_text("hello") is True
        mock_cli.assert_called_once_with(
            "send-text", "--pane-id", "42", input_text="hello",
        )

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_send_text_failure(self, mock_cli):
        mock_cli.return_value = _mock_result(returncode=1)
        session = PaneSession(42)
        assert session.send_text("hello") is False

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_is_alive_true(self, mock_cli):
        panes = [{"pane_id": 42}, {"pane_id": 99}]
        mock_cli.return_value = _mock_result(stdout=json.dumps(panes))
        session = PaneSession(42)
        assert session.is_alive() is True

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_is_alive_false(self, mock_cli):
        panes = [{"pane_id": 99}]
        mock_cli.return_value = _mock_result(stdout=json.dumps(panes))
        session = PaneSession(42)
        assert session.is_alive() is False

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_is_alive_cli_error(self, mock_cli):
        mock_cli.return_value = _mock_result(returncode=1)
        session = PaneSession(42)
        assert session.is_alive() is False

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_is_alive_bad_json(self, mock_cli):
        mock_cli.return_value = _mock_result(stdout="not json")
        session = PaneSession(42)
        assert session.is_alive() is False

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_kill(self, mock_cli):
        mock_cli.return_value = _mock_result()
        session = PaneSession(42)
        session.kill()
        mock_cli.assert_called_once_with("kill-pane", "--pane-id", "42")

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_spawn_success(self, mock_cli):
        mock_cli.return_value = _mock_result(stdout="55\n")
        session = PaneSession.spawn(["echo", "hi"], cwd="/tmp")
        assert session is not None
        assert session.pane_id == 55

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_spawn_without_cwd(self, mock_cli):
        mock_cli.return_value = _mock_result(stdout="10\n")
        session = PaneSession.spawn(["cmd"])
        assert session is not None
        args = mock_cli.call_args[0]
        assert "--cwd" not in args

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_spawn_failure(self, mock_cli):
        mock_cli.return_value = _mock_result(returncode=1)
        assert PaneSession.spawn(["cmd"]) is None

    @patch("repo_tools.agent.wezterm._run_cli")
    def test_spawn_bad_output(self, mock_cli):
        mock_cli.return_value = _mock_result(stdout="garbage")
        assert PaneSession.spawn(["cmd"]) is None


# ── _SendBuilder ──────────────────────────────────────────────────


class TestSendBuilder:
    def test_fluent_api_returns_self(self):
        session = MagicMock(spec=PaneSession)
        builder = _SendBuilder(session)
        result = builder.keys("\x1b").text("hello").pause(0.1)
        assert result is builder

    @patch("time.sleep")
    def test_send_executes_steps(self, mock_sleep):
        session = MagicMock(spec=PaneSession)
        builder = _SendBuilder(session)
        builder.keys("\x1b").text("hello").pause(0.5).keys("\r").send()

        session.send_keys.assert_any_call("\x1b")
        session.send_keys.assert_any_call("\r")
        session.send_text.assert_called_once_with("hello")
        mock_sleep.assert_called_once_with(0.5)

    @patch("time.sleep")
    def test_send_empty_builder(self, mock_sleep):
        session = MagicMock(spec=PaneSession)
        builder = _SendBuilder(session)
        builder.send()  # No steps, should not fail
        session.send_keys.assert_not_called()
        session.send_text.assert_not_called()

    def test_compose_input_returns_builder(self):
        session = PaneSession(1)
        builder = session.compose_input()
        assert isinstance(builder, _SendBuilder)
