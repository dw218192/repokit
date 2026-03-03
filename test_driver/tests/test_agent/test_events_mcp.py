"""Tests for the events MCP stdio server."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from repo_tools.agent.events_mcp import main

_MOD = "repo_tools.agent.events_mcp"
_NO_DEFAULTS = patch("repo_tools.core._CONFIG_DEFAULTS", Path("/nonexistent"))

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


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def project(tmp_path):
    """Create a project with sample config.yaml."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(SAMPLE_CONFIG), encoding="utf-8")
    return tmp_path


# ── Helper ────────────────────────────────────────────────────────


def _call(
    *requests: dict,
    project_root: str,
    signal_file: str,
    no_defaults: bool = True,
) -> list[dict]:
    """Run main() with the given requests, return parsed JSON responses."""
    lines = "\n".join(json.dumps(r) for r in requests) + "\n"
    captured = io.StringIO()
    ctx = (
        patch("sys.stdin", io.StringIO(lines)),
        patch("sys.stdout", captured),
        patch("sys.argv", [
            "events_mcp",
            "--project-root", project_root,
            "--signal-file", signal_file,
        ]),
    )
    if no_defaults:
        ctx = (*ctx, _NO_DEFAULTS)
    from contextlib import ExitStack
    with ExitStack() as stack:
        for cm in ctx:
            stack.enter_context(cm)
        main()
    output = captured.getvalue().strip()
    if not output:
        return []
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def _tool_call(
    project_root: str,
    signal_file: str,
    name: str,
    arguments: dict,
    no_defaults: bool = True,
) -> dict:
    """Call a single tool and return the result dict."""
    responses = _call(
        {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        project_root=project_root,
        signal_file=signal_file,
        no_defaults=no_defaults,
    )
    assert len(responses) == 1
    result = responses[0]["result"]
    text = result["content"][0]["text"]
    is_error = result.get("isError", False)
    return {"text": text, "isError": is_error}


# ── Protocol tests ────────────────────────────────────────────────


class TestProtocol:
    def test_initialize(self, project, tmp_path):
        responses = _call(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            project_root=str(project),
            signal_file=str(tmp_path / "sig.json"),
        )
        assert len(responses) == 1
        result = responses[0]["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "events"

    def test_tools_list(self, project, tmp_path):
        responses = _call(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            project_root=str(project),
            signal_file=str(tmp_path / "sig.json"),
        )
        tools = responses[0]["result"]["tools"]
        names = {t["name"] for t in tools}
        assert names == {"list_events", "subscribe"}

    def test_notification_no_response(self, project, tmp_path):
        responses = _call(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            project_root=str(project),
            signal_file=str(tmp_path / "sig.json"),
        )
        assert responses == []

    def test_unknown_method(self, project, tmp_path):
        responses = _call(
            {"jsonrpc": "2.0", "id": 1, "method": "bogus"},
            project_root=str(project),
            signal_file=str(tmp_path / "sig.json"),
        )
        assert responses[0]["error"]["code"] == -32601

    def test_unknown_tool(self, project, tmp_path):
        result = _tool_call(str(project), str(tmp_path / "sig.json"), "nonexistent", {})
        assert result["isError"]
        assert "Unknown tool" in result["text"]


# ── list_events ───────────────────────────────────────────────────


class TestListEvents:
    def test_returns_all_events(self, project, tmp_path):
        result = _tool_call(str(project), str(tmp_path / "sig.json"), "list_events", {})
        assert not result["isError"]
        text = result["text"]
        assert "repo.push" in text
        assert "repo.tag" in text
        assert "ci.complete" in text

    def test_returns_docs(self, project, tmp_path):
        result = _tool_call(str(project), str(tmp_path / "sig.json"), "list_events", {})
        assert "New commits pushed to a branch" in result["text"]
        assert "CI pipeline finished" in result["text"]

    def test_returns_param_info(self, project, tmp_path):
        result = _tool_call(str(project), str(tmp_path / "sig.json"), "list_events", {})
        assert "branch (required)" in result["text"]
        assert "remote (optional, default: 'origin')" in result["text"]

    def test_group_filter(self, project, tmp_path):
        result = _tool_call(
            str(project), str(tmp_path / "sig.json"),
            "list_events", {"group": "ci"},
        )
        assert not result["isError"]
        assert "ci.complete" in result["text"]
        assert "repo.push" not in result["text"]

    def test_group_filter_no_match(self, project, tmp_path):
        result = _tool_call(
            str(project), str(tmp_path / "sig.json"),
            "list_events", {"group": "nonexistent"},
        )
        assert "No events found" in result["text"]

    def test_empty_config_shows_builtins(self, tmp_path):
        """With empty project config, framework default events are still available."""
        project = tmp_path / "empty_proj"
        project.mkdir()
        (project / "config.yaml").write_text("{}", encoding="utf-8")
        result = _tool_call(
            str(project), str(tmp_path / "sig.json"), "list_events", {},
            no_defaults=False,
        )
        assert "github.ci_complete" in result["text"]

    def test_no_events_when_defaults_missing(self, tmp_path):
        """With no framework defaults and no project events, returns no events."""
        project = tmp_path / "empty_proj"
        project.mkdir()
        (project / "config.yaml").write_text("{}", encoding="utf-8")
        result = _tool_call(str(project), str(tmp_path / "sig.json"), "list_events", {})
        assert "No events defined" in result["text"]

    def test_grouped_output(self, project, tmp_path):
        result = _tool_call(str(project), str(tmp_path / "sig.json"), "list_events", {})
        text = result["text"]
        assert "[repo]" in text
        assert "[ci]" in text


# ── subscribe ─────────────────────────────────────────────────────


class TestSubscribe:
    def test_valid_subscription(self, project, tmp_path):
        sig = tmp_path / "sig.json"
        result = _tool_call(
            str(project), str(sig),
            "subscribe", {"event_type": "repo.push", "params": {"branch": "main"}},
        )
        assert not result["isError"]
        assert "Subscribed to repo.push" in result["text"]
        assert "suspend and resume" in result["text"]

    def test_writes_signal_file(self, project, tmp_path):
        sig = tmp_path / "sig.json"
        _tool_call(
            str(project), str(sig),
            "subscribe", {"event_type": "repo.push", "params": {"branch": "main"}},
        )
        assert sig.exists()
        data = json.loads(sig.read_text(encoding="utf-8"))
        assert data["event_type"] == "repo.push"
        assert data["params"]["branch"] == "main"

    def test_unknown_event_type(self, project, tmp_path):
        result = _tool_call(
            str(project), str(tmp_path / "sig.json"),
            "subscribe", {"event_type": "no.such.event", "params": {}},
        )
        assert result["isError"]
        assert "Unknown event type" in result["text"]

    def test_missing_required_param(self, project, tmp_path):
        result = _tool_call(
            str(project), str(tmp_path / "sig.json"),
            "subscribe", {"event_type": "repo.push", "params": {}},
        )
        assert result["isError"]
        assert "Missing required param" in result["text"]
        assert "branch" in result["text"]

    def test_optional_param_not_required(self, project, tmp_path):
        sig = tmp_path / "sig.json"
        result = _tool_call(
            str(project), str(sig),
            "subscribe", {"event_type": "repo.push", "params": {"branch": "main"}},
        )
        assert not result["isError"]

    def test_empty_event_type(self, project, tmp_path):
        result = _tool_call(
            str(project), str(tmp_path / "sig.json"),
            "subscribe", {"event_type": "", "params": {}},
        )
        assert result["isError"]
        assert "event_type is required" in result["text"]
