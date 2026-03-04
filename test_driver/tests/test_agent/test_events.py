"""Tests for repo_tools.agent.events — data model, config parsing, polling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_tools.agent.events import (
    EventDef,
    Subscription,
    _parse_events_section,
    collect_payload,
    expand_command,
    load_events,
    poll_delta,
    poll_exit,
    poll_for_event,
)


# ── Sample config ─────────────────────────────────────────────────

SAMPLE_CONFIG = {
    "events": {
        "repo": {
            "push": {
                "doc": "New commits pushed to a branch",
                "params": {
                    "branch": {"required": True},
                    "remote": {"default": "origin"},
                },
                "poll": "git ls-remote {remote} {branch}",
                "payload": "git log --oneline -5",
            },
            "tag": {
                "doc": "New tag created",
                "params": {
                    "pattern": {"required": True},
                },
                "poll": "git tag -l {pattern}",
                "payload": "git show {pattern}",
                "detect": "output_change",
                "poll_interval": 60,
            },
        },
        "ci": {
            "complete": {
                "doc": "CI pipeline finished",
                "params": {
                    "pipeline_id": {"required": True},
                    "status": {"default": "success"},
                },
                "poll": "check-ci {pipeline_id}",
                "payload": "get-ci-log {pipeline_id}",
            },
        },
    },
}


# ── load_events ───────────────────────────────────────────────────


class TestParseEventsSection:
    """Tests for _parse_events_section (pure config parsing, no built-ins)."""

    def test_parses_all_events(self):
        events = _parse_events_section(SAMPLE_CONFIG["events"])
        assert set(events.keys()) == {"repo.push", "repo.tag", "ci.complete"}

    def test_event_fields(self):
        events = _parse_events_section(SAMPLE_CONFIG["events"])
        push = events["repo.push"]
        assert push.group == "repo"
        assert push.name == "push"
        assert push.doc == "New commits pushed to a branch"
        assert push.poll == "git ls-remote {remote} {branch}"
        assert push.payload == "git log --oneline -5"
        assert push.detect == "exit"  # default
        assert push.poll_interval == 30  # default

    def test_custom_detect_and_interval(self):
        events = _parse_events_section(SAMPLE_CONFIG["events"])
        tag = events["repo.tag"]
        assert tag.detect == "output_change"
        assert tag.poll_interval == 60

    def test_params_parsed(self):
        events = _parse_events_section(SAMPLE_CONFIG["events"])
        push = events["repo.push"]
        assert push.params["branch"]["required"] is True
        assert push.params["remote"]["default"] == "origin"

    def test_empty_dict(self):
        assert _parse_events_section({}) == {}

    def test_non_dict(self):
        assert _parse_events_section("not a dict") == {}

    def test_non_dict_group(self):
        assert _parse_events_section({"repo": "not a dict"}) == {}

    def test_non_dict_event(self):
        assert _parse_events_section({"repo": {"push": "not a dict"}}) == {}


class TestLoadEvents:
    """Tests for load_events (pure config parser, no file I/O)."""

    def test_parses_events_section(self):
        events = load_events(SAMPLE_CONFIG)
        assert set(events.keys()) == {"repo.push", "repo.tag", "ci.complete"}

    def test_empty_config(self):
        assert load_events({}) == {}

    def test_no_events_key(self):
        assert load_events({"tools": {}}) == {}

    def test_non_dict_events_section(self):
        assert load_events({"events": "not a dict"}) == {}


# ── expand_command ────────────────────────────────────────────────


class TestExpandCommand:
    def test_single_param(self):
        result = expand_command("git ls-remote {branch}", {"branch": "main"})
        assert result == "git ls-remote main"

    def test_multiple_params(self):
        result = expand_command(
            "git ls-remote {remote} {branch}",
            {"remote": "origin", "branch": "main"},
        )
        assert result == "git ls-remote origin main"

    def test_no_placeholders(self):
        result = expand_command("echo hello", {"unused": "val"})
        assert result == "echo hello"

    def test_repeated_placeholder(self):
        result = expand_command("{x} and {x}", {"x": "foo"})
        assert result == "foo and foo"

    def test_missing_param_left_as_is(self):
        result = expand_command("{missing}", {})
        assert result == "{missing}"

    def test_non_string_param_value(self):
        result = expand_command("poll --interval {n}", {"n": 42})
        assert result == "poll --interval 42"


# ── poll_exit ─────────────────────────────────────────────────────


class TestPollExit:
    @patch("repo_tools.agent.events.subprocess.run")
    def test_returns_zero_on_success(self, mock_run):
        mock_run.return_value.returncode = 0
        cwd = Path("/repo")
        assert poll_exit("gh run watch 123", cwd) == 0
        mock_run.assert_called_once_with("gh run watch 123", shell=True, cwd=str(cwd))

    @patch("repo_tools.agent.events.subprocess.run")
    def test_returns_nonzero_on_failure(self, mock_run):
        mock_run.return_value.returncode = 1
        assert poll_exit("false", Path("/repo")) == 1


# ── poll_delta ────────────────────────────────────────────────────


class TestPollDelta:
    @patch("repo_tools.agent.events.time.sleep")
    @patch("repo_tools.agent.events.subprocess.run")
    def test_detects_output_change(self, mock_run, mock_sleep):
        """Returns new stdout when output changes on second poll."""
        run1 = MagicMock(stdout="5\n")
        run2 = MagicMock(stdout="6\n")
        mock_run.side_effect = [run1, run2]

        result = poll_delta("git rev-list --count HEAD", 10, Path("/repo"))
        assert result == "6\n"
        mock_sleep.assert_called_once_with(10)

    @patch("repo_tools.agent.events.time.sleep")
    @patch("repo_tools.agent.events.subprocess.run")
    def test_keeps_polling_when_stable(self, mock_run, mock_sleep):
        """Continues polling when output hasn't changed yet."""
        stable = MagicMock(stdout="same")
        changed = MagicMock(stdout="different")
        # initial, then two stable re-polls, then change
        mock_run.side_effect = [stable, stable, stable, changed]

        result = poll_delta("cmd", 5, Path("/repo"))
        assert result == "different"
        assert mock_sleep.call_count == 3

    @patch("repo_tools.agent.events.time.sleep")
    @patch("repo_tools.agent.events.subprocess.run")
    def test_respects_interval(self, mock_run, mock_sleep):
        """Sleeps for the specified interval between polls."""
        r1 = MagicMock(stdout="a")
        r2 = MagicMock(stdout="b")
        mock_run.side_effect = [r1, r2]

        poll_delta("cmd", 42, Path("/repo"))
        mock_sleep.assert_called_with(42)


# ── collect_payload ───────────────────────────────────────────────


class TestCollectPayload:
    @patch("repo_tools.agent.events.subprocess.run")
    def test_returns_stdout(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "  payload data  \n"
        mock_run.return_value.stderr = ""
        assert collect_payload("get-log 42", Path("/repo")) == "payload data"

    @patch("repo_tools.agent.events.subprocess.run")
    def test_handles_failure_with_stderr(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "command not found\n"
        result = collect_payload("bad-cmd", Path("/repo"))
        assert result == "command not found"

    @patch("repo_tools.agent.events.subprocess.run")
    def test_handles_failure_without_stderr(self, mock_run):
        mock_run.return_value.returncode = 2
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        result = collect_payload("bad-cmd", Path("/repo"))
        assert "failed" in result.lower()


# ── poll_for_event ────────────────────────────────────────────────


class TestPollForEvent:
    @patch("repo_tools.agent.events.collect_payload", return_value="log output")
    @patch("repo_tools.agent.events.poll_exit", return_value=0)
    def test_dispatches_exit_mode(self, mock_poll_exit, mock_payload):
        ev = EventDef(
            group="ci", name="done", doc="", params={},
            poll="check-ci {id}", payload="get-log {id}", detect="exit",
        )
        sub = Subscription(event_type="ci.done", params={"id": "99"})

        result = poll_for_event(ev, sub, Path("/repo"))
        mock_poll_exit.assert_called_once_with("check-ci 99", Path("/repo"))
        mock_payload.assert_called_once_with("get-log 99", Path("/repo"))
        assert result == "log output"

    @patch("repo_tools.agent.events.collect_payload", return_value="new data")
    @patch("repo_tools.agent.events.poll_delta", return_value="changed")
    def test_dispatches_delta_mode(self, mock_poll_delta, mock_payload):
        ev = EventDef(
            group="repo", name="push", doc="", params={},
            poll="git ls-remote {branch}", payload="git log {branch}",
            detect="delta", poll_interval=15,
        )
        sub = Subscription(event_type="repo.push", params={"branch": "main"})

        result = poll_for_event(ev, sub, Path("/repo"))
        mock_poll_delta.assert_called_once_with("git ls-remote main", 15, Path("/repo"))
        mock_payload.assert_called_once_with("git log main", Path("/repo"))
        assert result == "new data"

    @patch("repo_tools.agent.events.poll_exit", return_value=0)
    def test_no_payload_returns_status(self, mock_poll_exit):
        ev = EventDef(
            group="ci", name="done", doc="", params={},
            poll="wait-for {id}", payload="", detect="exit",
        )
        sub = Subscription(event_type="ci.done", params={"id": "1"})

        result = poll_for_event(ev, sub, Path("/repo"))
        assert result == "Event fired."

    @patch("repo_tools.agent.events.poll_exit", return_value=0)
    def test_expands_params(self, mock_poll_exit):
        ev = EventDef(
            group="ci", name="done", doc="", params={},
            poll="check {x} --flag {y}", payload="", detect="exit",
        )
        sub = Subscription(event_type="ci.done", params={"x": "A", "y": "B"})

        poll_for_event(ev, sub, Path("/repo"))
        mock_poll_exit.assert_called_once_with("check A --flag B", Path("/repo"))

    @patch("repo_tools.agent.events.poll_exit", return_value=1)
    def test_exit_mode_raises_on_failure(self, mock_poll_exit):
        ev = EventDef(
            group="ci", name="done", doc="", params={},
            poll="bad-cmd", payload="", detect="exit",
        )
        sub = Subscription(event_type="ci.done", params={})

        with pytest.raises(RuntimeError, match="Poll command failed"):
            poll_for_event(ev, sub, Path("/repo"))
