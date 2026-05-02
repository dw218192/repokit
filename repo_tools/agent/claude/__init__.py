"""Claude Code backend package."""

from __future__ import annotations

from ._cli import CliBackend

Claude = CliBackend


__all__ = ["Claude", "CliBackend"]
