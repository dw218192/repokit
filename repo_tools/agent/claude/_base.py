"""ClaudeBackend protocol — structural typing for backend implementations."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClaudeBackend(Protocol):
    """Protocol that both SDK and CLI backends implement."""

    def run_headless(
        self,
        *,
        prompt: str,
        role: str,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        project_root: Path | None = None,
        tool_config: dict | None = None,
        cwd: Path | str | None = None,
    ) -> tuple[str, int]: ...

    def run_interactive(
        self,
        *,
        role_prompt: str | None = None,
        rules_path: Path | None = None,
        project_root: Path | None = None,
        tool_config: dict | None = None,
        cwd: Path | str | None = None,
        initial_prompt: str | None = None,
        resume: str | None = None,
    ) -> tuple[int, str | None]: ...
