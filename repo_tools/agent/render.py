"""Rich-based message renderer for interactive agent sessions."""

from __future__ import annotations

from typing import Any

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel

    _RICH = True
except ImportError:
    _RICH = False

_console: Any = None


def _get_console() -> Any:
    global _console
    if _console is None and _RICH:
        _console = Console()
    return _console


def render_message(msg: Any) -> None:
    """Render a single SDK message to the terminal."""
    from claude_agent_sdk import AssistantMessage, ResultMessage
    from claude_agent_sdk.types import (
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    console = _get_console()

    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                if console and _RICH:
                    console.print(Markdown(block.text))
                else:
                    print(block.text)
            elif isinstance(block, ToolUseBlock):
                label = f"Tool: {block.name}"
                if console and _RICH:
                    console.print(Panel(label, style="dim"))
                else:
                    print(f"[{label}]")
            elif isinstance(block, ToolResultBlock):
                text = str(block.content or "")[:500]
                if console and _RICH:
                    console.print(text, style="dim")
                else:
                    print(text)
            elif isinstance(block, ThinkingBlock):
                pass  # hidden in non-verbose mode

    elif isinstance(msg, ResultMessage):
        parts = [f"Done ({msg.subtype})"]
        if msg.total_cost_usd is not None:
            parts.append(f"${msg.total_cost_usd:.4f}")
        parts.append(f"{msg.num_turns} turns")
        summary = " — ".join(parts)
        if console and _RICH:
            console.print(summary, style="bold")
        else:
            print(summary)
