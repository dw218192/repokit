"""Tests for repo_tools.agent.events — data model, config parsing, signal I/O."""

from __future__ import annotations

from pathlib import Path

from repo_tools.agent.events import (
    EventDef,
    Subscription,
    expand_command,
    load_events,
    read_signal,
    write_signal,
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


class TestLoadEvents:
    def test_parses_all_events(self):
        events = load_events(SAMPLE_CONFIG)
        assert set(events.keys()) == {"repo.push", "repo.tag", "ci.complete"}

    def test_event_fields(self):
        events = load_events(SAMPLE_CONFIG)
        push = events["repo.push"]
        assert push.group == "repo"
        assert push.name == "push"
        assert push.doc == "New commits pushed to a branch"
        assert push.poll == "git ls-remote {remote} {branch}"
        assert push.payload == "git log --oneline -5"
        assert push.detect == "exit"  # default
        assert push.poll_interval == 30  # default

    def test_custom_detect_and_interval(self):
        events = load_events(SAMPLE_CONFIG)
        tag = events["repo.tag"]
        assert tag.detect == "output_change"
        assert tag.poll_interval == 60

    def test_params_parsed(self):
        events = load_events(SAMPLE_CONFIG)
        push = events["repo.push"]
        assert push.params["branch"]["required"] is True
        assert push.params["remote"]["default"] == "origin"

    def test_empty_config(self):
        assert load_events({}) == {}

    def test_no_events_key(self):
        assert load_events({"tools": {}}) == {}

    def test_non_dict_events_section(self):
        assert load_events({"events": "not a dict"}) == {}

    def test_non_dict_group(self):
        events = load_events({"events": {"repo": "not a dict"}})
        assert events == {}

    def test_non_dict_event(self):
        events = load_events({"events": {"repo": {"push": "not a dict"}}})
        assert events == {}


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


# ── write_signal / read_signal ────────────────────────────────────


class TestSignalIO:
    def test_roundtrip(self, tmp_path):
        sig = tmp_path / "signal.json"
        sub = Subscription(event_type="repo.push", params={"branch": "main"})
        write_signal(sig, sub)

        result = read_signal(sig)
        assert result is not None
        assert result.event_type == "repo.push"
        assert result.params == {"branch": "main"}

    def test_read_signal_deletes_file(self, tmp_path):
        sig = tmp_path / "signal.json"
        write_signal(sig, Subscription(event_type="ci.complete", params={}))
        assert sig.exists()

        read_signal(sig)
        assert not sig.exists()

    def test_read_signal_missing_file(self, tmp_path):
        sig = tmp_path / "does_not_exist.json"
        assert read_signal(sig) is None

    def test_write_creates_parent_dirs(self, tmp_path):
        sig = tmp_path / "nested" / "deep" / "signal.json"
        write_signal(sig, Subscription(event_type="test", params={}))
        assert sig.exists()

    def test_roundtrip_empty_params(self, tmp_path):
        sig = tmp_path / "signal.json"
        write_signal(sig, Subscription(event_type="x.y", params={}))
        result = read_signal(sig)
        assert result is not None
        assert result.params == {}
