"""Tests for the Stop hook (repo_tools.agent.hooks.stop_hook)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


class TestStopHook:
    def _run_main(self, env: dict, port: int = 18042) -> None:
        import io
        import sys

        from repo_tools.agent.hooks.stop_hook import main

        stdin_data = json.dumps({"session_id": "abc"})
        with (
            patch("sys.argv", ["stop_hook", "--port", str(port)]),
            patch.dict("os.environ", env, clear=False),
            patch("sys.stdin", io.StringIO(stdin_data)),
        ):
            main()

    @patch("repo_tools.agent.hooks.stop_hook.urllib.request.urlopen")
    def test_posts_idle_with_pane_id(self, mock_urlopen, monkeypatch):
        """When WEZTERM_PANE is set, POSTs pane_id to /idle."""
        monkeypatch.setenv("WEZTERM_PANE", "42")
        self._run_main({"WEZTERM_PANE": "42"}, port=18042)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert "/idle" in req.full_url
        assert "18042" in req.full_url
        body = json.loads(req.data)
        assert body["pane_id"] == 42

    @patch("repo_tools.agent.hooks.stop_hook.urllib.request.urlopen")
    def test_no_pane_id_skips_post(self, mock_urlopen, monkeypatch):
        """When WEZTERM_PANE is absent, nothing is posted."""
        monkeypatch.delenv("WEZTERM_PANE", raising=False)
        self._run_main({})
        mock_urlopen.assert_not_called()

    @patch("repo_tools.agent.hooks.stop_hook.urllib.request.urlopen",
           side_effect=OSError("connection refused"))
    def test_silently_ignores_server_errors(self, mock_urlopen, monkeypatch):
        """Network errors are swallowed — never breaks the Stop hook."""
        monkeypatch.setenv("WEZTERM_PANE", "42")
        self._run_main({"WEZTERM_PANE": "42"})  # Should not raise

    @patch("repo_tools.agent.hooks.stop_hook.urllib.request.urlopen")
    def test_invalid_json_stdin_is_ignored(self, mock_urlopen, monkeypatch):
        """Bad JSON on stdin is swallowed; hook continues normally."""
        import io
        from repo_tools.agent.hooks.stop_hook import main

        monkeypatch.setenv("WEZTERM_PANE", "42")
        with (
            patch("sys.argv", ["stop_hook", "--port", "18042"]),
            patch("sys.stdin", io.StringIO("not valid json {")),
        ):
            main()  # Should not raise
        mock_urlopen.assert_called_once()  # still proceeds to POST

    @patch("repo_tools.agent.hooks.stop_hook.urllib.request.urlopen")
    def test_non_integer_pane_id_skips_post(self, mock_urlopen, monkeypatch):
        """Non-integer WEZTERM_PANE is silently ignored; nothing is posted."""
        monkeypatch.setenv("WEZTERM_PANE", "abc")
        self._run_main({"WEZTERM_PANE": "abc"})
        mock_urlopen.assert_not_called()
