"""Textual TUI for interactive agent sessions.

Renders tool calls in collapsible widgets, keeping the chat log scannable.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rich.markdown import Markdown
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual import work
from textual.widgets import Collapsible, Static, TextArea


# ── Widgets ───────────────────────────────────────────────────────────────────


class UserMessage(Static):
    """Submitted user prompt, styled distinctly."""

    DEFAULT_CSS = """
    UserMessage {
        margin: 0 1;
        padding: 0 1;
        color: $text;
        background: $boost;
        text-style: bold;
    }
    """


class MarkdownMessage(Static):
    """Assistant markdown text block."""

    DEFAULT_CSS = """
    MarkdownMessage {
        margin: 0 1;
        padding: 0 1;
    }
    """


class ToolCallCollapsible(Collapsible):
    """Wraps a tool use + result pair.

    Starts expanded with "Running..." placeholder.
    Collapses when the result arrives.
    """

    DEFAULT_CSS = """
    ToolCallCollapsible {
        margin: 0 1;
    }
    """

    def __init__(self, tool_name: str, **kwargs: Any) -> None:
        super().__init__(title=f"Tool: {tool_name}", collapsed=False, **kwargs)
        self._tool_name = tool_name

    def compose(self) -> ComposeResult:
        yield Static("Running...", id="tool-body")

    def set_result(self, text: str, is_error: bool = False) -> None:
        """Update body with truncated result and collapse."""
        truncated = text[:500]
        body = self.query_one("#tool-body", Static)
        if is_error:
            body.update(f"[red]{truncated}[/red]")
        else:
            body.update(truncated)
        self.collapsed = True


class StatusBar(Static):
    """Docked bar showing cost/turns summary."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("Ready", **kwargs)


class PromptInput(TextArea):
    r"""Multi-line prompt. Enter submits, ``\`` + Enter for continuation."""

    DEFAULT_CSS = """
    PromptInput {
        dock: bottom;
        height: auto;
        min-height: 3;
        max-height: 10;
    }
    """

    class Submitted(Message):
        """Posted when the user presses Enter to submit."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    async def _on_key(self, event: events.Key) -> None:
        r"""Intercept Enter before TextArea inserts a newline.

        ``\`` + Enter: remove the backslash and insert a real newline.
        Plain Enter: submit the accumulated text.
        Other keys: delegate to TextArea.
        """
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            text = self.text
            if text.endswith("\\"):
                # Replace the trailing backslash with a newline
                lines = text.split("\n")
                last_row = len(lines) - 1
                last_col = len(lines[-1])
                self._replace_via_keyboard(
                    "\n", (last_row, last_col - 1), (last_row, last_col),
                )
            elif text.strip():
                submitted = text
                self.clear()
                self.post_message(self.Submitted(submitted))
            return
        await super()._on_key(event)


# ── App ───────────────────────────────────────────────────────────────────────


_SENTINEL = object()


class AgentApp(App):
    """Textual TUI for interactive Claude agent sessions.

    The SDK client lifecycle is owned by a single background worker
    (_client_loop) so that __aenter__/__aexit__ run in the same asyncio
    task — required by anyio's cancel-scope rules.
    """

    CSS = """
    #chat-log {
        height: 1fr;
        padding: 1 0 0 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel_or_quit", "Cancel/Quit", show=False),
        Binding("ctrl+d", "quit", "Quit", show=False),
        Binding("ctrl+l", "clear_log", "Clear", show=False),
    ]

    def __init__(self, options: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._options = options
        self._client: Any = None
        self._query_active = False
        self._pending_tools: dict[str, ToolCallCollapsible] = {}
        self._queued_input: list[str] = []
        self._input_queue: asyncio.Queue[Any] = asyncio.Queue()

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-log")
        yield StatusBar(id="status-bar")
        yield PromptInput(id="prompt-input")

    async def on_mount(self) -> None:
        self._client_loop()
        self.query_one("#prompt-input", PromptInput).focus()

    # ── Client lifecycle worker ───────────────────────────────────────────────

    @work(group="client")
    async def _client_loop(self) -> None:
        """Own the SDK client in a single task (enter + exit same task)."""
        from claude_agent_sdk import ClaudeSDKClient

        async with ClaudeSDKClient(options=self._options) as client:
            self._client = client
            try:
                while True:
                    user_input = await self._input_queue.get()
                    if user_input is _SENTINEL:
                        break
                    self._query_active = True
                    try:
                        await client.query(user_input)
                        async for msg in client.receive_response():
                            await self._handle_message(msg)
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        self.query_one("#status-bar", StatusBar).update(
                            "Error",
                        )
                    finally:
                        self._query_active = False
                        self._drain_queue()
            except asyncio.CancelledError:
                pass
        self._client = None

    # ── Input handling ────────────────────────────────────────────────────────

    async def on_prompt_input_submitted(
        self, event: PromptInput.Submitted,
    ) -> None:
        text = event.text

        chat_log = self.query_one("#chat-log", VerticalScroll)
        msg_widget = UserMessage(f"> {text}")
        await chat_log.mount(msg_widget)
        msg_widget.scroll_visible()

        if self._query_active:
            self._queued_input.append(text)
            return

        self._input_queue.put_nowait(text)

    def _drain_queue(self) -> None:
        """Send the next queued input, if any."""
        if self._queued_input:
            self._input_queue.put_nowait(self._queued_input.pop(0))

    # ── Message handling ──────────────────────────────────────────────────────

    async def _handle_message(self, msg: Any) -> None:
        from claude_agent_sdk import AssistantMessage, ResultMessage
        from claude_agent_sdk.types import (
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
        )

        chat_log = self.query_one("#chat-log", VerticalScroll)

        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    widget = MarkdownMessage("")
                    widget.update(Markdown(block.text))
                    await chat_log.mount(widget)
                elif isinstance(block, ToolUseBlock):
                    collapsible = ToolCallCollapsible(block.name)
                    self._pending_tools[block.id] = collapsible
                    await chat_log.mount(collapsible)
                elif isinstance(block, ToolResultBlock):
                    collapsible = self._pending_tools.pop(
                        block.tool_use_id, None,
                    )
                    if collapsible:
                        text = str(block.content or "")
                        collapsible.set_result(
                            text, is_error=bool(block.is_error),
                        )
                elif isinstance(block, ThinkingBlock):
                    pass

        elif isinstance(msg, ResultMessage):
            parts = [f"Done ({msg.subtype})"]
            if msg.total_cost_usd is not None:
                parts.append(f"${msg.total_cost_usd:.4f}")
            parts.append(f"{msg.num_turns} turns")
            self.query_one("#status-bar", StatusBar).update(
                " — ".join(parts),
            )

        chat_log.scroll_end(animate=False)

    # ── Key bindings ──────────────────────────────────────────────────────────

    def action_cancel_or_quit(self) -> None:
        if self._query_active:
            self._queued_input.clear()
            if self._client and hasattr(self._client, "abort"):
                self._client.abort()
            self.query_one("#status-bar", StatusBar).update("Cancelled")
        else:
            self._input_queue.put_nowait(_SENTINEL)
            self.exit()

    def action_clear_log(self) -> None:
        if not self._query_active:
            self.query_one("#chat-log", VerticalScroll).remove_children()
