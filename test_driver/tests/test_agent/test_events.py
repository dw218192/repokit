"""Tests for the event-driven session wrapper."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from repo_tools.agent.events import (
    TOOL_SCHEMAS,
    clear_subscriptions,
    has_subscriptions,
    list_events_text,
    poll_until_fired,
    pop_subscription,
    resolve_event_config,
    run_event_steps,
    run_payload,
    subscribe,
)


@pytest.fixture(autouse=True)
def _clean_subscriptions():
    """Ensure subscriptions are empty before and after each test."""
    clear_subscriptions()
    yield
    clear_subscriptions()


# ── Config fixtures ──────────────────────────────────────────────────────────

_CONFIG = {
    "agent": {
        "events": {
            "timer": {
                "tick": {
                    "poll": ["echo ok"],
                    "payload": ["echo payload-data"],
                    "interval": 5,
                    "description": "Periodic timer",
                },
            },
            "github": {
                "pr_opened": {
                    "poll": ["gh pr list"],
                    "payload": ["gh pr list --json"],
                    "interval": 60,
                    "description": "New pull request opened",
                },
                "check_failed": {
                    "poll": ["gh run list"],
                    "payload": ["gh run list --json"],
                    "interval": 120,
                    "description": "Check run failed",
                },
            },
        },
    },
}


# ── Subscription FIFO ────────────────────────────────────────────────────────


def test_subscribe_and_pop():
    subscribe("timer", "tick", _CONFIG)
    subscribe("github", "pr_opened", _CONFIG, tokens={"gh_repo": "org/repo"})
    assert has_subscriptions()

    sub1 = pop_subscription()
    assert sub1["group"] == "timer"
    assert sub1["event"] == "tick"

    sub2 = pop_subscription()
    assert sub2["group"] == "github"
    assert sub2["event"] == "pr_opened"
    assert sub2["tokens"]["gh_repo"] == "org/repo"

    assert not has_subscriptions()
    assert pop_subscription() is None


def test_subscribe_returns_message():
    msg = subscribe("timer", "tick", _CONFIG)
    assert "Subscribed" in msg
    assert "timer.tick" in msg


def test_subscribe_invalid_group():
    with pytest.raises(KeyError, match="Unknown event group"):
        subscribe("nonexistent", "tick", _CONFIG)


def test_subscribe_invalid_event():
    with pytest.raises(KeyError, match="Unknown event"):
        subscribe("timer", "nonexistent", _CONFIG)


def test_clear_subscriptions():
    subscribe("timer", "tick", _CONFIG)
    subscribe("timer", "tick", _CONFIG)
    clear_subscriptions()
    assert not has_subscriptions()


# ── Config lookup ────────────────────────────────────────────────────────────


def test_resolve_event_config():
    cfg = resolve_event_config(_CONFIG, "timer", "tick")
    assert cfg["poll"] == ["echo ok"]
    assert cfg["interval"] == 5


def test_resolve_event_config_missing_group():
    with pytest.raises(KeyError, match="Unknown event group"):
        resolve_event_config(_CONFIG, "deploy", "done")


def test_resolve_event_config_missing_event():
    with pytest.raises(KeyError, match="Unknown event.*in group"):
        resolve_event_config(_CONFIG, "timer", "nonexistent")


def test_resolve_no_events():
    with pytest.raises(KeyError, match="Unknown event group"):
        resolve_event_config({"agent": {}}, "timer", "tick")


# ── Flat config (as backends pass it) ───────────────────────────────────────

_FLAT_CONFIG = _CONFIG["agent"]  # {"events": {"timer": ..., "github": ...}}


def test_resolve_event_config_flat():
    """Backends pass flat agent config — events lookup must still work."""
    cfg = resolve_event_config(_FLAT_CONFIG, "timer", "tick")
    assert cfg["poll"] == ["echo ok"]


def test_list_events_text_flat():
    """list_events_text must work with flat agent config."""
    text = list_events_text(_FLAT_CONFIG)
    assert "timer" in text
    assert "tick" in text
    assert "github" in text


def test_subscribe_flat():
    """subscribe must work with flat agent config."""
    msg = subscribe("timer", "tick", _FLAT_CONFIG)
    assert "Subscribed" in msg
    assert has_subscriptions()


# ── list_events_text ─────────────────────────────────────────────────────────


def test_list_events_text():
    text = list_events_text(_CONFIG)
    assert "timer" in text
    assert "tick" in text
    assert "Periodic timer" in text
    assert "github" in text
    assert "pr_opened" in text


def test_list_events_text_no_events():
    assert list_events_text({}) == "No events configured."
    assert list_events_text({"agent": {}}) == "No events configured."


# ── run_event_steps ──────────────────────────────────────────────────────────


def test_run_event_steps_success(tmp_path):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "hello\n"

    with patch("repo_tools.agent.events.ShellCommand") as mock_sc:
        mock_sc.return_value.run.return_value = mock_proc
        rc, stdout = run_event_steps(
            ["echo hello"], {}, {}, str(tmp_path),
        )

    assert rc == 0
    assert stdout == "hello\n"


def test_run_event_steps_failure(tmp_path):
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = "error\n"

    with patch("repo_tools.agent.events.ShellCommand") as mock_sc:
        mock_sc.return_value.run.return_value = mock_proc
        rc, stdout = run_event_steps(
            ["fail-command"], {}, {}, str(tmp_path),
        )

    assert rc == 1


def test_run_event_steps_token_expansion(tmp_path):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "ok"

    captured_cmds = []

    def _fake_sc(args, **kwargs):
        captured_cmds.append(args)
        mock = MagicMock()
        mock.run.return_value = mock_proc
        return mock

    with patch("repo_tools.agent.events.ShellCommand", side_effect=_fake_sc):
        run_event_steps(
            ["echo {my_token}"],
            {"my_token": "expanded-value"},
            {},
            str(tmp_path),
        )

    assert len(captured_cmds) == 1
    assert "expanded-value" in " ".join(captured_cmds[0])


# ── poll_until_fired ─────────────────────────────────────────────────────────


def test_poll_until_fired_immediate(tmp_path):
    """Poll returns 0 immediately — no sleep needed."""
    event_cfg = {"poll": ["echo ok"], "interval": 1}

    with patch("repo_tools.agent.events.run_event_steps", return_value=(0, "")) as mock:
        poll_until_fired(event_cfg, {}, {}, str(tmp_path))

    mock.assert_called_once()


def test_poll_until_fired_retry(tmp_path):
    """Poll returns non-zero first, then 0 — should retry."""
    event_cfg = {"poll": ["check-status"], "interval": 0.01}

    call_count = 0

    def _fake_run(steps, tokens, config, cwd):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return (1, "")
        return (0, "")

    with (
        patch("repo_tools.agent.events.run_event_steps", side_effect=_fake_run),
        patch("time.sleep"),
    ):
        poll_until_fired(event_cfg, {}, {}, str(tmp_path))

    assert call_count == 3


def test_poll_default_interval():
    """Default interval is 60 when not specified."""
    event_cfg = {"poll": ["check"], "payload": ["data"]}
    assert event_cfg.get("interval", 60) == 60


# ── run_payload ──────────────────────────────────────────────────────────────


def test_run_payload(tmp_path):
    event_cfg = {"payload": ["echo data"], "poll": ["echo ok"]}

    with patch(
        "repo_tools.agent.events.run_event_steps",
        return_value=(0, "  payload-data  \n"),
    ):
        result = run_payload(event_cfg, {}, {}, str(tmp_path))

    assert result == "payload-data"


# ── Tool schemas ─────────────────────────────────────────────────────────────


def test_tool_schemas():
    names = [s["name"] for s in TOOL_SCHEMAS]
    assert "list_events" in names
    assert "subscribe_event" in names

    sub_schema = next(s for s in TOOL_SCHEMAS if s["name"] == "subscribe_event")
    assert "group" in sub_schema["inputSchema"]["properties"]
    assert "event" in sub_schema["inputSchema"]["properties"]
    assert sub_schema["inputSchema"]["required"] == ["group", "event"]
