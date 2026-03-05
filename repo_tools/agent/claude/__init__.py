"""Claude Code backend package.

Re-exports the backend protocol, implementations, and hook factories
so existing imports (``from repo_tools.agent.claude import Claude``)
continue to work.
"""

from __future__ import annotations

from ._base import ClaudeBackend
from ._cli import CliBackend
from ._hooks import _make_approve_mcp_hook, _make_check_bash_hook
from ._sdk import SdkBackend, _make_coderabbit_tool, _make_lint_tool, _make_ticket_tools

# Backward-compatible alias used by tests.
Claude = SdkBackend


def get_backend(preference: str | None = None) -> ClaudeBackend:
    """Return a backend instance based on preference or auto-detection.

    ``preference`` can be ``"cli"``, ``"sdk"``, or ``None`` (auto-detect).
    Auto-detect tries to import ``claude_agent_sdk``; if available, returns
    ``SdkBackend``, otherwise falls back to ``CliBackend``.
    """
    if preference == "cli":
        return CliBackend()
    if preference == "sdk":
        return SdkBackend()
    # Auto: try SDK import, fall back to CLI
    try:
        import claude_agent_sdk  # noqa: F401
        return SdkBackend()
    except ImportError:
        return CliBackend()


__all__ = [
    "Claude",
    "ClaudeBackend",
    "CliBackend",
    "SdkBackend",
    "get_backend",
    "_make_approve_mcp_hook",
    "_make_check_bash_hook",
    "_make_coderabbit_tool",
    "_make_lint_tool",
    "_make_ticket_tools",
]
