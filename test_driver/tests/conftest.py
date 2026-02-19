"""Shared fixtures for repokit tests."""

from __future__ import annotations

import io
import logging
import textwrap
from pathlib import Path
from typing import Any

import pytest

from repo_tools import core
from repo_tools.core import ToolContext, resolve_tokens


@pytest.fixture(autouse=True)
def reset_tool_registry():
    """Save and restore _TOOL_REGISTRY around each test."""
    saved = core._TOOL_REGISTRY.copy()
    yield
    core._TOOL_REGISTRY.clear()
    core._TOOL_REGISTRY.update(saved)


@pytest.fixture
def make_workspace(tmp_path: Path):
    """Factory that creates a temp workspace simulating a consumer project.

    Usage::

        ws = make_workspace(config_yaml=\"\"\"
            tokens:
                custom: hello
        \"\"\")
    """
    _counter = 0

    def _make(
        config_yaml: str | None = None,
        project_tool_files: dict[str, str] | None = None,
    ) -> Path:
        nonlocal _counter
        ws = tmp_path / f"workspace_{_counter}"
        ws.mkdir()
        _counter += 1

        if config_yaml is not None:
            (ws / "config.yaml").write_text(
                textwrap.dedent(config_yaml), encoding="utf-8",
            )

        if project_tool_files:
            pt_dir = ws / "tools" / "repo_tools"
            pt_dir.mkdir(parents=True)
            for filename, content in project_tool_files.items():
                (pt_dir / filename).write_text(
                    textwrap.dedent(content), encoding="utf-8",
                )

        return ws

    return _make


@pytest.fixture
def make_tool_context(tmp_path: Path):
    """Factory to build a ToolContext for unit-testing tools directly.

    Usage::

        ctx = make_tool_context(tool_config={"command": "echo hi"})
        tool.execute(ctx, args)
    """

    def _make(
        config: dict[str, Any] | None = None,
        tool_config: dict[str, Any] | None = None,
        dimensions: dict[str, str] | None = None,
        tokens_override: dict[str, str] | None = None,
        passthrough_args: list[str] | None = None,
        workspace_root: Path | None = None,
    ) -> ToolContext:
        ws = workspace_root or tmp_path / "ws"
        ws.mkdir(exist_ok=True)

        cfg = config or {}
        dims = dimensions or {"platform": "linux-x64", "build_type": "Debug"}

        tokens = resolve_tokens(str(ws), cfg, dims)
        if tokens_override:
            tokens.update(tokens_override)

        return ToolContext(
            workspace_root=ws,
            tokens=tokens,
            config=cfg,
            tool_config=tool_config or {},
            dimensions=dims,
            passthrough_args=passthrough_args or [],
        )

    return _make


@pytest.fixture
def capture_logs():
    """Capture repo_tools logger output into a StringIO buffer.

    The logger has propagate=False and its own StreamHandler that points
    at the original sys.stderr fd, so capsys/capfd/caplog cannot see it.
    This fixture adds a temporary StringIO handler.

    Usage::

        buf = capture_logs()
        # ... run code that logs ...
        assert "expected" in buf.getvalue()
    """
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("repo_tools")
    logger.addHandler(handler)
    yield buf
    logger.removeHandler(handler)
