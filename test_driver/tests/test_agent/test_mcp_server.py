"""Tests for TeamMCPServer and related utilities in repo_tools.agent.mcp_server."""

from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_tools.agent.mcp_server import (
    DEFAULT_REMINDER_INTERVAL,
    DEFAULT_REMINDER_LIMIT,
    PaneState,
    TeamMCPServer,
    _ThreadingHTTPServer,
    find_free_port,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _post(port: int, path: str, data: dict, headers: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(data).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers=h,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except OSError:
            raw = b""
        return e.code, json.loads(raw) if raw.strip() else {}


@pytest.fixture
def live_server():
    """Start a TeamMCPServer HTTP listener on a free port; yield (server, port)."""
    port = find_free_port()
    server = TeamMCPServer("ws1", port, reminder_interval=60, reminder_limit=3)
    handler_cls = server._make_handler()
    httpd = _ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield server, port
    httpd.shutdown()


# ── find_free_port ────────────────────────────────────────────────────────────


def test_find_free_port_returns_valid_port():
    port = find_free_port()
    assert 1024 <= port <= 65535


def test_find_free_port_unique():
    p1 = find_free_port()
    p2 = find_free_port()
    assert isinstance(p1, int) and isinstance(p2, int)


# ── PaneState defaults ────────────────────────────────────────────────────────


def test_pane_state_defaults():
    s = PaneState(pane_id=1, role="worker", workstream="ws1", ticket="G1_1")
    assert s.idle_since is None
    assert s.reminder_count == 0
    assert s.last_reminder is None


# ── Registration ──────────────────────────────────────────────────────────────


class TestRegistration:
    def _srv(self):
        return TeamMCPServer("ws1", 19000)

    def test_register_pane(self):
        srv = self._srv()
        srv.register_pane(42, "worker", "ws1", "G1_1")
        assert 42 in srv._panes
        assert srv._key_map[("ws1", "G1_1")] == 42

    def test_deregister_pane(self):
        srv = self._srv()
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv.deregister_pane(42)
        assert 42 not in srv._panes
        assert ("ws1", "G1_1") not in srv._key_map

    def test_deregister_nonexistent_is_noop(self):
        srv = self._srv()
        srv.deregister_pane(999)


# ── Idle signalling ───────────────────────────────────────────────────────────


class TestIdleSignalling:
    def _srv(self):
        return TeamMCPServer("ws1", 19001)

    def test_notify_idle_sets_idle_since(self):
        srv = self._srv()
        srv.register_pane(42, "worker", "ws1", "G1_1")
        before = time.monotonic()
        srv.notify_idle(42)
        assert srv._panes[42].idle_since is not None
        assert srv._panes[42].idle_since >= before

    def test_notify_idle_does_not_override_existing(self):
        srv = self._srv()
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv.notify_idle(42)
        first = srv._panes[42].idle_since
        srv.notify_idle(42)
        assert srv._panes[42].idle_since == first

    def test_notify_idle_unknown_pane_ignored(self):
        srv = self._srv()
        srv.notify_idle(999)

    def test_notify_active_resets_idle(self):
        srv = self._srv()
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv.notify_idle(42)
        srv._panes[42].reminder_count = 2
        srv.notify_active("ws1", "G1_1")
        state = srv._panes[42]
        assert state.idle_since is None
        assert state.reminder_count == 0
        assert state.last_reminder is None

    def test_notify_active_unknown_key_ignored(self):
        srv = self._srv()
        srv.notify_active("ws1", "no-such-ticket")


# ── Watchdog ──────────────────────────────────────────────────────────────────


class TestWatchdog:
    def _srv(self, interval=10, limit=3):
        return TeamMCPServer("ws1", 19002, reminder_interval=interval, reminder_limit=limit)

    @patch("repo_tools.agent.mcp_server.PaneSession")
    def test_sends_reminder_after_interval(self, mock_pane_cls):
        srv = self._srv(interval=5, limit=3)
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv._panes[42].idle_since = time.monotonic() - 10

        session = MagicMock()
        mock_pane_cls.return_value = session

        srv._watchdog_tick()

        mock_pane_cls.assert_called_with(42)
        session.compose_input.assert_called()
        assert srv._panes[42].reminder_count == 1

    @patch("repo_tools.agent.mcp_server.PaneSession")
    def test_reminder_exception_is_swallowed(self, mock_pane_cls):
        """Reminder failures must not crash the watchdog."""
        srv = self._srv(interval=5, limit=3)
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv._panes[42].idle_since = time.monotonic() - 10
        mock_pane_cls.return_value.compose_input.side_effect = OSError("broken")

        srv._watchdog_tick()  # Should not raise
        assert srv._panes[42].reminder_count == 1

    @patch("repo_tools.agent.mcp_server.PaneSession")
    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_kills_after_limit(self, mock_list, mock_pane_cls):
        mock_list.return_value = [{"pane_id": 1}]
        srv = self._srv(interval=5, limit=2)
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv._panes[42].idle_since = time.monotonic() - 10
        srv._panes[42].reminder_count = 1
        srv._panes[42].last_reminder = time.monotonic() - 10

        orch = MagicMock()
        worker = MagicMock()
        mock_pane_cls.side_effect = lambda pid: orch if pid == 1 else worker

        srv._watchdog_tick()

        worker.kill.assert_called_once()
        assert 42 not in srv._panes
        orch.compose_input.assert_called()

    @patch("repo_tools.agent.mcp_server.PaneSession")
    def test_no_action_when_not_idle(self, mock_pane_cls):
        srv = self._srv(interval=5, limit=3)
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv._watchdog_tick()
        mock_pane_cls.assert_not_called()

    @patch("repo_tools.agent.mcp_server.PaneSession")
    def test_respects_interval(self, mock_pane_cls):
        srv = self._srv(interval=300, limit=3)
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv._panes[42].idle_since = time.monotonic() - 5
        srv._watchdog_tick()
        mock_pane_cls.assert_not_called()
        assert srv._panes[42].reminder_count == 0

    @patch("repo_tools.agent.mcp_server.PaneSession")
    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_kill_stalled_already_deregistered_is_noop(self, mock_list, mock_pane_cls):
        """Race: if pane vanished before _kill_stalled runs, nothing happens."""
        mock_list.return_value = []
        srv = self._srv()
        # Call _kill_stalled for a pane that was never registered
        srv._kill_stalled(999, "ws1", "G1_1", 3)
        mock_pane_cls.assert_not_called()

    @patch("repo_tools.agent.mcp_server.PaneSession")
    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_kill_stalled_pane_kill_exception_swallowed(self, mock_list, mock_pane_cls):
        """Failure to kill the WezTerm pane must not abort the kill sequence."""
        mock_list.return_value = [{"pane_id": 1}]
        orch = MagicMock()
        worker = MagicMock()
        worker.kill.side_effect = OSError("gone")
        mock_pane_cls.side_effect = lambda pid: orch if pid == 1 else worker

        srv = self._srv()
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv._kill_stalled(42, "ws1", "G1_1", 3)

        # Pane still deregistered, orchestrator still notified
        assert 42 not in srv._panes
        orch.compose_input.assert_called()

    @patch("repo_tools.agent.mcp_server.PaneSession")
    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_kill_stalled_orchestrator_notify_exception_swallowed(self, mock_list, mock_pane_cls):
        """Failure to notify orchestrator must not propagate."""
        mock_list.side_effect = OSError("wezterm gone")
        worker = MagicMock()
        mock_pane_cls.return_value = worker

        srv = self._srv()
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv._kill_stalled(42, "ws1", "G1_1", 3)  # Should not raise
        assert 42 not in srv._panes


# ── send_message ──────────────────────────────────────────────────────────────


class TestSendMessage:
    @patch("repo_tools.agent.mcp_server.PaneSession")
    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_send_to_orchestrator(self, mock_list, mock_pane_cls):
        mock_list.return_value = [{"pane_id": 5}]
        mock_pane_cls.return_value = MagicMock()
        srv = TeamMCPServer("ws1", 19003)
        result = srv._call_send_message({
            "target": "orchestrator", "workstream": "ws1",
            "message": "TICKET G1_1: status=verify",
        })
        assert result.get("isError") is not True

    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_no_panes_returns_error(self, mock_list):
        mock_list.return_value = []
        srv = TeamMCPServer("ws1", 19004)
        result = srv._call_send_message({"target": "orchestrator", "workstream": "ws1", "message": "hi"})
        assert result.get("isError") is True

    @patch("repo_tools.agent.mcp_server.PaneSession")
    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_numeric_target_found(self, mock_list, mock_pane_cls):
        mock_list.return_value = [{"pane_id": 5}, {"pane_id": 9}]
        mock_pane_cls.return_value = MagicMock()
        srv = TeamMCPServer("ws1", 19005)
        result = srv._call_send_message({"target": "9", "workstream": "ws1", "message": "hi"})
        assert result.get("isError") is not True
        mock_pane_cls.assert_called_with(9)

    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_numeric_target_not_found(self, mock_list):
        mock_list.return_value = [{"pane_id": 5}]
        srv = TeamMCPServer("ws1", 19006)
        result = srv._call_send_message({"target": "99", "workstream": "ws1", "message": "hi"})
        assert result.get("isError") is True
        assert "not found" in result["text"]

    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_invalid_target_returns_error(self, mock_list):
        mock_list.return_value = [{"pane_id": 5}]
        srv = TeamMCPServer("ws1", 19007)
        result = srv._call_send_message({"target": "not-a-number", "workstream": "ws1", "message": "hi"})
        assert result.get("isError") is True
        assert "Invalid target" in result["text"]

    @patch("repo_tools.agent.mcp_server.PaneSession")
    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_send_resets_idle_for_ticket(self, mock_list, mock_pane_cls):
        mock_list.return_value = [{"pane_id": 1}]
        mock_pane_cls.return_value = MagicMock()
        srv = TeamMCPServer("ws1", 19008)
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv._panes[42].idle_since = time.monotonic()
        srv._panes[42].reminder_count = 2
        srv._call_send_message({
            "target": "orchestrator", "workstream": "ws1",
            "ticket": "G1_1", "message": "TICKET G1_1: status=verify",
        })
        assert srv._panes[42].idle_since is None
        assert srv._panes[42].reminder_count == 0

    @patch("repo_tools.agent.mcp_server.threading.Timer")
    @patch("repo_tools.agent.mcp_server.PaneSession")
    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_done_true_schedules_cleanup(self, mock_list, mock_pane_cls, mock_timer):
        """done=true schedules a Timer to kill and deregister the caller's pane."""
        mock_list.return_value = [{"pane_id": 1}]
        mock_pane_cls.return_value = MagicMock()
        mock_timer_instance = MagicMock()
        mock_timer.return_value = mock_timer_instance

        srv = TeamMCPServer("ws1", 19009)
        srv.register_pane(42, "worker", "ws1", "G1_1")
        result = srv._call_send_message({
            "target": "orchestrator", "workstream": "ws1",
            "ticket": "G1_1", "done": True,
            "message": "TICKET G1_1: status=verify",
        })

        assert result.get("isError") is not True
        mock_timer.assert_called_once()
        delay, fn, *_ = mock_timer.call_args[0]
        assert delay == pytest.approx(5.0)
        assert fn == srv._cleanup_pane
        mock_timer_instance.start.assert_called_once()

    @patch("repo_tools.agent.mcp_server.threading.Timer")
    @patch("repo_tools.agent.mcp_server.PaneSession")
    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_done_false_no_cleanup(self, mock_list, mock_pane_cls, mock_timer):
        """done=false (default) does not schedule a cleanup timer."""
        mock_list.return_value = [{"pane_id": 1}]
        mock_pane_cls.return_value = MagicMock()
        srv = TeamMCPServer("ws1", 19010)
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv._call_send_message({
            "target": "orchestrator", "workstream": "ws1",
            "ticket": "G1_1", "message": "TICKET G1_1: status=verify",
        })
        mock_timer.assert_not_called()

    @patch("repo_tools.agent.mcp_server.PaneSession")
    def test_cleanup_pane_kills_and_deregisters(self, mock_pane_cls):
        """_cleanup_pane kills the pane and removes it from tracking."""
        mock_session = MagicMock()
        mock_pane_cls.return_value = mock_session
        srv = TeamMCPServer("ws1", 19011)
        srv.register_pane(42, "worker", "ws1", "G1_1")

        srv._cleanup_pane(42)

        mock_session.kill.assert_called_once()
        assert 42 not in srv._panes

    @patch("repo_tools.agent.mcp_server.PaneSession")
    def test_cleanup_pane_already_gone_is_noop(self, mock_pane_cls):
        """_cleanup_pane is a no-op if the pane was already removed."""
        srv = TeamMCPServer("ws1", 19012)
        srv._cleanup_pane(999)  # never registered — should not raise
        mock_pane_cls.assert_not_called()

    @patch("repo_tools.agent.mcp_server.PaneSession")
    def test_cleanup_pane_swallows_kill_error(self, mock_pane_cls):
        """_cleanup_pane deregisters even if kill() raises."""
        mock_pane_cls.return_value.kill.side_effect = OSError("gone")
        srv = TeamMCPServer("ws1", 19013)
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv._cleanup_pane(42)  # should not raise
        assert 42 not in srv._panes

    @patch("repo_tools.agent.mcp_server.threading.Timer")
    @patch("repo_tools.agent.mcp_server.PaneSession")
    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_done_without_ticket_no_cleanup(self, mock_list, mock_pane_cls, mock_timer):
        """done=true with no ticket cannot identify a pane — no Timer scheduled."""
        mock_list.return_value = [{"pane_id": 1}]
        mock_pane_cls.return_value = MagicMock()
        srv = TeamMCPServer("ws1", 19014)
        srv._call_send_message({
            "target": "orchestrator", "workstream": "ws1",
            "done": True, "message": "general update",
        })
        mock_timer.assert_not_called()


# ── _call_coderabbit_review ───────────────────────────────────────────────────


class TestCallCoderabbitReview:
    _MOD = "repo_tools.agent.mcp_server"

    @pytest.fixture(autouse=True)
    def _skip_path_check(self):
        """These tests exercise non-path behavior; bypass is_dir validation."""
        with patch.object(Path, "is_dir", return_value=True):
            yield

    def _srv(self):
        return TeamMCPServer("ws1", 19100)

    def test_missing_worktree_path(self):
        result = self._srv()._call_coderabbit_review({})
        assert result["isError"] is True
        assert "worktree_path" in result["text"]

    @patch("repo_tools.agent.coderabbit.check_installed", return_value=False)
    def test_not_installed(self, _):
        result = self._srv()._call_coderabbit_review({"worktree_path": "/tmp/wt"})
        assert result["isError"] is True
        assert "not installed" in result["text"]
        assert "install" in result["text"].lower()

    @patch("repo_tools.agent.coderabbit.subprocess.run")
    @patch("repo_tools.agent.coderabbit.check_installed", return_value=True)
    def test_not_authenticated(self, _, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not logged in")
        result = self._srv()._call_coderabbit_review({"worktree_path": "/tmp/wt"})
        assert result["isError"] is True
        assert "not authenticated" in result["text"]
        assert "login" in result["text"].lower()

    @patch("repo_tools.agent.coderabbit.subprocess.run")
    @patch("repo_tools.agent.coderabbit.check_installed", return_value=True)
    def test_auth_check_exception(self, _, mock_run):
        mock_run.side_effect = OSError("spawn error")
        result = self._srv()._call_coderabbit_review({"worktree_path": "/tmp/wt"})
        assert result["isError"] is True
        assert "fall back" in result["text"].lower()

    @patch("repo_tools.agent.coderabbit.subprocess.run")
    @patch("repo_tools.agent.coderabbit.check_installed", return_value=True)
    def test_review_returns_output(self, _, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="auth ok", stderr=""),   # auth check
            MagicMock(returncode=0, stdout="Line 3: potential null deref", stderr=""),  # review
        ]
        result = self._srv()._call_coderabbit_review({"worktree_path": "/tmp/wt"})
        assert result.get("isError") is not True
        assert "null deref" in result["text"]

    @patch("repo_tools.agent.coderabbit.subprocess.run")
    @patch("repo_tools.agent.coderabbit.check_installed", return_value=True)
    def test_review_empty_output(self, _, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        result = self._srv()._call_coderabbit_review({"worktree_path": "/tmp/wt"})
        assert result.get("isError") is not True
        assert "No issues" in result["text"]

    @patch("repo_tools.agent.coderabbit.subprocess.run")
    @patch("repo_tools.agent.coderabbit.check_installed", return_value=True)
    def test_review_timeout(self, _, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),
            subprocess.TimeoutExpired(cmd="coderabbit", timeout=120),
        ]
        result = self._srv()._call_coderabbit_review({"worktree_path": "/tmp/wt"})
        assert result["isError"] is True
        assert "timed out" in result["text"]

    @patch("repo_tools.agent.coderabbit.subprocess.run")
    @patch("repo_tools.agent.coderabbit.check_installed", return_value=True)
    def test_review_exception(self, _, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),
            OSError("process failed"),
        ]
        result = self._srv()._call_coderabbit_review({"worktree_path": "/tmp/wt"})
        assert result["isError"] is True
        assert "fall back" in result["text"].lower()

    @patch("repo_tools.agent.coderabbit.subprocess.run")
    @patch("repo_tools.agent.coderabbit.check_installed", return_value=True)
    def test_custom_type_passed_to_cli(self, _, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=0, stdout="clean", stderr=""),
        ]
        self._srv()._call_coderabbit_review({"worktree_path": "/tmp/wt", "type": "uncommitted"})
        review_cmd_str = " ".join(mock_run.call_args_list[1][0][0])
        assert "--type" in review_cmd_str
        assert "uncommitted" in review_cmd_str

    def test_windows_wsl_not_installed(self):
        """On Windows, 'command -v coderabbit' returning non-zero means not installed."""
        wsl_fail = MagicMock(returncode=1)
        with (
            patch("repo_tools.agent.coderabbit.is_windows", return_value=True),
            patch("repo_tools.agent.coderabbit.subprocess.run", return_value=wsl_fail),
        ):
            result = self._srv()._call_coderabbit_review({"worktree_path": "/tmp/wt"})
        assert result["isError"] is True
        assert "not installed" in result["text"]
        assert "Windows" in result["text"]

    def test_windows_uses_wsl_prefix(self):
        """On Windows, all coderabbit subprocess commands go through 'wsl bash -lc'."""
        install_ok = MagicMock(returncode=0)
        auth_ok = MagicMock(returncode=0, stdout="", stderr="")
        review_ok = MagicMock(returncode=0, stdout="clean", stderr="")

        captured: list = []

        def _se(*args, **kw):
            captured.append(args[0])
            if "command -v" in " ".join(args[0]):
                return install_ok
            if "auth" in " ".join(args[0]):
                return auth_ok
            return review_ok

        with (
            patch("repo_tools.agent.coderabbit.is_windows", return_value=True),
            patch("repo_tools.agent.coderabbit.subprocess.run", side_effect=_se),
        ):
            self._srv()._call_coderabbit_review({"worktree_path": "/tmp/wt"})

        assert len(captured) == 3
        for cmd in captured:
            assert cmd[0] == "wsl", f"Expected 'wsl' prefix, got: {cmd}"
            assert cmd[1] == "bash", f"Expected 'bash' login shell, got: {cmd}"


# ── HTTP coderabbit_review via live server ────────────────────────────────────


class TestHTTPCoderabbitReview:
    @pytest.fixture(autouse=True)
    def _skip_path_check(self):
        with patch.object(Path, "is_dir", return_value=True):
            yield

    @patch("repo_tools.agent.coderabbit.subprocess.run")
    @patch("repo_tools.agent.coderabbit.check_installed", return_value=True)
    def test_coderabbit_review_tool_via_mcp(self, _, mock_run, live_server):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=0, stdout="LGTM", stderr=""),
        ]
        _, port = live_server
        status, body = _post(port, "/", {
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {
                "name": "coderabbit_review",
                "arguments": {"worktree_path": "/tmp/wt"},
            },
        })
        assert status == 200
        assert body["result"]["content"][0]["text"] == "LGTM"

    @patch("repo_tools.agent.coderabbit.check_installed", return_value=False)
    def test_coderabbit_not_installed_via_mcp(self, _, live_server):
        _, port = live_server
        status, body = _post(port, "/", {
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "coderabbit_review", "arguments": {"worktree_path": "/tmp/wt"}},
        })
        assert status == 200
        assert body["result"]["isError"] is True

    def test_tools_list_includes_coderabbit(self, live_server):
        _, port = live_server
        status, body = _post(port, "/", {"jsonrpc": "2.0", "id": 12, "method": "tools/list"})
        assert status == 200
        names = [t["name"] for t in body["result"]["tools"]]
        assert "coderabbit_review" in names
        assert "send_message" in names


# ── HTTP handler (via live server) ────────────────────────────────────────────


class TestHTTPHandler:
    def test_register_endpoint(self, live_server):
        server, port = live_server
        status, body = _post(port, "/register", {
            "pane_id": 55, "role": "worker", "workstream": "ws1", "ticket": "G1_1",
        })
        assert status == 200
        assert body == {"ok": True}
        assert 55 in server._panes

    def test_idle_endpoint(self, live_server):
        server, port = live_server
        server.register_pane(55, "worker", "ws1", "G1_1")
        status, _ = _post(port, "/idle", {"pane_id": 55})
        assert status == 200
        assert server._panes[55].idle_since is not None

    def test_unknown_path_returns_404(self, live_server):
        _, port = live_server
        status, body = _post(port, "/not-a-thing", {})
        assert status == 404

    def test_invalid_json_returns_400(self, live_server):
        _, port = live_server
        raw = b"not json"
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/",
            data=raw,
            headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 400

    def test_mcp_notification_returns_202(self, live_server):
        """JSON-RPC notifications (no 'id') return 202 Accepted."""
        _, port = live_server
        status, _ = _post(port, "/", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert status == 202

    def test_mcp_initialize(self, live_server):
        _, port = live_server
        status, body = _post(port, "/", {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
        })
        assert status == 200
        result = body["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == "repokit-team"

    def test_mcp_ping(self, live_server):
        _, port = live_server
        status, body = _post(port, "/", {"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert status == 200
        assert body["result"] == {}

    def test_mcp_tools_list(self, live_server):
        _, port = live_server
        status, body = _post(port, "/", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        assert status == 200
        tools = body["result"]["tools"]
        assert any(t["name"] == "send_message" for t in tools)

    @patch("repo_tools.agent.mcp_server.PaneSession")
    @patch("repo_tools.agent.mcp_server.list_workspace")
    def test_mcp_tools_call_send_message(self, mock_list, mock_pane_cls, live_server):
        mock_list.return_value = [{"pane_id": 1}]
        mock_pane_cls.return_value = MagicMock()
        _, port = live_server
        status, body = _post(port, "/", {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {
                "name": "send_message",
                "arguments": {"workstream": "ws1", "message": "hello"},
            },
        })
        assert status == 200
        content = body["result"]["content"]
        assert any("sent" in c.get("text", "").lower() for c in content)

    def test_mcp_tools_call_unknown_tool(self, live_server):
        _, port = live_server
        status, body = _post(port, "/", {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "no_such_tool", "arguments": {}},
        })
        assert status == 200
        assert body["result"].get("isError") is True

    def test_mcp_unknown_method_returns_error(self, live_server):
        _, port = live_server
        status, body = _post(port, "/", {"jsonrpc": "2.0", "id": 6, "method": "mystery/call"})
        assert status == 200
        assert "error" in body
        assert body["error"]["code"] == -32601

    def test_mcp_path_slash_mcp(self, live_server):
        """Requests to /mcp are handled identically to /."""
        _, port = live_server
        status, body = _post(port, "/mcp", {"jsonrpc": "2.0", "id": 7, "method": "ping"})
        assert status == 200


# ── run() and _shutdown_all_panes() ──────────────────────────────────────────


class TestLifecycle:
    @patch("repo_tools.agent.mcp_server.PaneSession")
    def test_shutdown_kills_all_panes(self, mock_pane_cls):
        sessions = {42: MagicMock(), 43: MagicMock()}
        mock_pane_cls.side_effect = lambda pid: sessions[pid]

        srv = TeamMCPServer("ws1", 19020)
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv.register_pane(43, "reviewer", "ws1", "G1_2")
        srv._shutdown_all_panes()

        sessions[42].kill.assert_called_once()
        sessions[43].kill.assert_called_once()

    @patch("repo_tools.agent.mcp_server.PaneSession")
    def test_shutdown_swallows_kill_errors(self, mock_pane_cls):
        mock_pane_cls.return_value.kill.side_effect = OSError("gone")
        srv = TeamMCPServer("ws1", 19021)
        srv.register_pane(42, "worker", "ws1", "G1_1")
        srv._shutdown_all_panes()  # Should not raise

    def test_run_starts_and_stops(self):
        """run() binds the port, starts watchdog, and shuts down cleanly."""
        port = find_free_port()
        srv = TeamMCPServer("ws1", port)

        def _trigger_shutdown():
            time.sleep(0.1)
            if srv._http:
                srv._http.shutdown()

        t = threading.Thread(target=_trigger_shutdown, daemon=True)
        t.start()
        srv.run()   # Should return once _http.shutdown() is called
        t.join(timeout=2)
        assert srv._http is not None  # was set during run()

    @patch("repo_tools.agent.mcp_server.PaneSession")
    def test_run_keyboard_interrupt_triggers_shutdown(self, mock_pane_cls):
        """KeyboardInterrupt from serve_forever triggers the finally cleanup path."""
        mock_pane_cls.return_value.kill.return_value = None

        port = find_free_port()
        srv = TeamMCPServer("ws1", port)
        srv.register_pane(77, "worker", "ws1", "T1")

        mock_httpd = MagicMock()
        mock_httpd.serve_forever.side_effect = KeyboardInterrupt()

        with patch("repo_tools.agent.mcp_server._ThreadingHTTPServer", return_value=mock_httpd):
            srv.run()

        mock_httpd.shutdown.assert_called_once()
        mock_pane_cls.return_value.kill.assert_called_once()


# ── Body size limit (Issue 3) ─────────────────────────────────────────────────


def _post_raw(port: int, path: str, raw: bytes, headers: dict | None = None) -> int:
    """POST raw bytes and return the HTTP status code."""
    h = {"Content-Type": "application/json", "Content-Length": str(len(raw))}
    if headers:
        h.update(headers)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=raw,
        headers=h,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


class TestBodySizeLimit:
    def test_oversized_body_returns_413(self, live_server):
        """A body exceeding _MAX_BODY_SIZE should return 413.

        The handler rejects based on the Content-Length header before reading
        the body, so we send a small body with an oversized Content-Length to
        avoid a Windows connection-abort when pushing >1 MiB over localhost.
        """
        _, port = live_server
        from repo_tools.agent.mcp_server import _MAX_BODY_SIZE
        status = _post_raw(
            port, "/register", b"{}",
            headers={"Content-Length": str(_MAX_BODY_SIZE + 1)},
        )
        assert status == 413


# ── Error handling on /register and /idle (Issue 4) ───────────────────────────


class TestEndpointErrorHandling:
    def test_register_missing_keys_returns_400(self, live_server):
        _, port = live_server
        status, body = _post(port, "/register", {"pane_id": 1})
        assert status == 400

    def test_register_invalid_json_returns_400(self, live_server):
        _, port = live_server
        status = _post_raw(port, "/register", b"not json at all")
        assert status == 400

    def test_register_bad_pane_id_type_returns_400(self, live_server):
        _, port = live_server
        status, body = _post(port, "/register", {
            "pane_id": "not-a-number",
            "role": "worker",
            "workstream": "ws1",
            "ticket": "T1",
        })
        assert status == 400

    def test_idle_missing_pane_id_returns_400(self, live_server):
        _, port = live_server
        status, body = _post(port, "/idle", {})
        assert status == 400

    def test_idle_invalid_json_returns_400(self, live_server):
        _, port = live_server
        status = _post_raw(port, "/idle", b"{bad json")
        assert status == 400


# ── worktree_path validation (Issue 6) ────────────────────────────────────────


class TestWorktreePathValidation:
    def test_nonexistent_worktree_path_returns_error(self):
        srv = TeamMCPServer("ws1", 19200)
        result = srv._call_coderabbit_review({"worktree_path": "/nonexistent/path/12345"})
        assert result["isError"] is True
        assert "not a directory" in result["text"]
