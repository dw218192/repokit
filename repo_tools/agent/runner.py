"""Abstract base class for CLI-based agent tools."""

from __future__ import annotations

from abc import ABC, abstractmethod


class AgentCLITool(ABC):
    """Backend descriptor for a terminal-based coding agent.

    Knows how to build the launch command.  Does NOT own the session
    lifecycle — that belongs to the agent tool.
    """

    @abstractmethod
    def build_command(self, **kwargs) -> list[str]:
        """Return the CLI command to launch this agent.

        Keyword arguments may include:
        - role: str | None — team role name
        - role_prompt: str | None — additional prompt text for the role
        """
