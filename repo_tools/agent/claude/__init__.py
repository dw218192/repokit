"""Claude Code backend package."""

from __future__ import annotations

from ._cli import CliBackend

# Backward-compatible alias used by tests.
Claude = CliBackend


__all__ = ["Claude", "CliBackend"]
