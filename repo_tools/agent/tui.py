"""Textual TUI for interactive agent sessions.

Tool calls appear as one-line status indicators in the chat log and as
collapsible entries in a rolling Tools side pane.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Suppress a known CPython bug on Windows: _ProactorBasePipeTransport.__del__
# calls __repr__ which calls fileno() on an already-closed pipe, raising
# ValueError during GC after shutdown.
if sys.platform == "win32":
    _original_unraisablehook = sys.unraisablehook

    def _quiet_unraisablehook(unraisable: sys.UnraisableHookArgs) -> None:
        if isinstance(unraisable.exc_value, ValueError) and \
                "closed pipe" in str(unraisable.exc_value):
            return
        _original_unraisablehook(unraisable)

    sys.unraisablehook = _quiet_unraisablehook

from rich.markdown import Markdown
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual import work
from textual.widgets import (
    Collapsible,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)


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


class ToolCallGroup(Static):
    """Compact inline summary of consecutive tool calls.

    Shows: "\u25b8 Bash \u2713 Read \u2717 Edit" — one token per tool, all on one line.
    Reused across consecutive tool calls; a new group starts after a TextBlock.
    """

    DEFAULT_CSS = "ToolCallGroup { margin: 0 1; color: $text-muted; }"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._tools: dict[str, tuple[str, str]] = {}  # id → (name, icon)

    def add_tool(self, tool_id: str, name: str) -> None:
        self._tools[tool_id] = (name, "\u25b8")
        self._refresh_display()

    def set_result(self, tool_id: str, is_error: bool = False) -> None:
        name, _ = self._tools[tool_id]
        self._tools[tool_id] = (name, "\u2717" if is_error else "\u2713")
        self._refresh_display()

    def _refresh_display(self) -> None:
        parts = [f"{icon} {name}" for name, icon in self._tools.values()]
        self.update("  ".join(parts))


class StatusBar(Static):
    """Docked bar showing cost/turns summary. Green=idle, yellow=working."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        padding: 0 1;
        color: $text;
    }
    StatusBar.status-ready { background: green; }
    StatusBar.status-working { background: darkorange; }
    StatusBar.status-error { background: red; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("Ready", **kwargs)
        self.add_class("status-ready")

    def set_status(self, text: str, state: str = "ready") -> None:
        """Update text and color. state: ready, working, or error."""
        self.update(text)
        self.remove_class("status-ready", "status-working", "status-error")
        self.add_class(f"status-{state}")


class QueueBar(Static):
    """Shows queued messages as a numbered list. Hidden when empty."""

    DEFAULT_CSS = """
    QueueBar {
        height: auto;
        max-height: 6;
        background: $surface;
        color: $text-muted;
        display: none;
    }
    """

    def refresh_queue(self, items: list[str]) -> None:
        """Update display. Shows/hides based on whether items exist."""
        if items:
            header = f"Queued ({len(items)})"
            lines = [header]
            for i, item in enumerate(items, 1):
                lines.append(f"  [{i}] {item}")
            self.update("\n".join(lines))
            self.display = True
        else:
            self.update("")
            self.display = False


def _ticket_to_markdown(data: dict) -> str:
    """Convert ticket JSON to markdown for display."""
    lines: list[str] = []
    for key, val in data.items():
        if key == "id":
            continue
        if isinstance(val, list):
            if val:
                lines.append(f"**{key}:**")
                for item in val:
                    lines.append(f"- {item}")
            else:
                lines.append(f"**{key}:** (none)")
        elif isinstance(val, dict):
            if val:
                lines.append(f"**{key}:**")
                for k, v in val.items():
                    lines.append(f"- {k}: {v}")
            else:
                lines.append(f"**{key}:** (none)")
        else:
            lines.append(f"**{key}:** {val}")
    return "\n".join(lines) if lines else "(empty)"


class TicketPanel(VerticalScroll):
    """Displays tickets from _agent/tickets/ as collapsible color-coded cards."""

    DEFAULT_CSS = """
    TicketPanel { height: 1fr; }
    TicketPanel .ticket-todo CollapsibleTitle { color: yellow; }
    TicketPanel .ticket-in-progress CollapsibleTitle { color: dodgerblue; }
    TicketPanel .ticket-verify CollapsibleTitle { color: magenta; }
    TicketPanel .ticket-closed CollapsibleTitle { color: green; }
    """

    _STATUS_ICONS: dict[str, str] = {
        "todo": "\u25cb",
        "in_progress": "\u25c9",
        "verify": "\u25ce",
        "closed": "\u25cf",
    }

    def __init__(self, workspace: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._workspace = workspace or os.getcwd()

    def on_mount(self) -> None:
        self.refresh_tickets()

    def refresh_tickets(self) -> None:
        """Re-scan ticket directory and rebuild the panel."""
        self.remove_children()
        ticket_dir = Path(self._workspace) / "_agent" / "tickets"
        if not ticket_dir.is_dir():
            self.mount(Static("(no tickets)"))
            return
        for path in sorted(ticket_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._add_ticket(path.stem, data)
            except Exception:
                logger.warning("Failed to load ticket %s", path, exc_info=True)
                self.mount(Static(f"{path.stem} (error)"))

    def _add_ticket(self, ticket_id: str, data: dict) -> None:
        status = data.get("status", "")
        icon = self._STATUS_ICONS.get(status, "\u25cb")
        md_text = _ticket_to_markdown(data)
        body = Static(Markdown(md_text))
        entry = Collapsible(
            body, title=f"{icon} {ticket_id}", collapsed=True,
        )
        if status:
            entry.add_class(f"ticket-{status.replace('_', '-')}")
        self.mount(entry)


def _summarize_tool(name: str, input_args: dict | None) -> str:
    """One-line summary like ``Bash(cd /c/repo && ls)``."""
    if not input_args:
        return f"{name}()"
    if name == "Bash":
        arg = input_args.get("command", "")
    elif name in ("Read", "Edit", "Write"):
        arg = input_args.get("file_path", "")
    elif name == "Glob":
        arg = input_args.get("pattern", "")
    elif name == "Grep":
        arg = input_args.get("pattern", "")
    else:
        arg = next(
            (str(v) for v in input_args.values() if isinstance(v, str)),
            "...",
        )
    if len(arg) > 60:
        arg = arg[:57] + "..."
    return f"{name}({arg})"


class ToolLog(VerticalScroll):
    """Rolling log of tool calls with collapsible arg/output details."""

    DEFAULT_CSS = """
    ToolLog {
        height: 1fr;
    }
    ToolLog Collapsible { width: 100%; }
    ToolLog .tool-pending CollapsibleTitle { color: yellow; }
    ToolLog .tool-success CollapsibleTitle { color: green; }
    ToolLog .tool-error CollapsibleTitle { color: red; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # tool_id → (Collapsible, summary_text)
        self._entries: dict[str, tuple[Collapsible, str]] = {}

    def add_tool(
        self, tool_id: str, name: str, input_args: dict | None,
    ) -> None:
        """Register a new tool call as a collapsed collapsible entry."""
        summary = _summarize_tool(name, input_args)
        body_text = ""
        if input_args:
            try:
                body_text = json.dumps(input_args, indent=2)[:500]
            except (TypeError, ValueError):
                body_text = str(input_args)[:500]
        entry = Collapsible(
            Static(body_text or "(no args)", classes="tool-log-body"),
            title=f"\u23f3 {summary}",
            collapsed=True,
        )
        entry.add_class("tool-pending")
        self._entries[tool_id] = (entry, summary)
        self.mount(entry)
        self.scroll_end(animate=False)

    def set_result(
        self, tool_id: str, output: str, is_error: bool = False,
    ) -> None:
        """Update an entry with its result: set icon, append output."""
        pair = self._entries.get(tool_id)
        if pair is None:
            return
        entry, summary = pair
        icon = "\u2717" if is_error else "\u2713"
        entry.title = f"{icon} {summary}"
        entry.remove_class("tool-pending")
        entry.add_class("tool-error" if is_error else "tool-success")
        truncated = (output or "")[:500]
        try:
            body = entry.query_one(".tool-log-body", Static)
            current = str(body.renderable)
            if is_error:
                body.update(f"{current}\n[red]{truncated}[/red]")
            else:
                body.update(f"{current}\n{truncated}")
        except Exception:
            logger.debug("ToolLog.set_result: body update failed", exc_info=True)
        self.scroll_end(animate=False)


class PromptInput(TextArea):
    r"""Multi-line prompt. Enter submits, ``\`` + Enter for continuation."""

    DEFAULT_CSS = """
    PromptInput {
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


class TUILogHandler(logging.Handler):
    """Bridge Python logging to a Textual RichLog widget."""

    def __init__(self, widget: RichLog) -> None:
        super().__init__()
        self._widget = widget

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._widget.write(self.format(record))
        except Exception:
            pass


# ── App ───────────────────────────────────────────────────────────────────────


_SENTINEL = object()


class AgentApp(App):
    """Textual TUI for interactive Claude agent sessions.

    The SDK client lifecycle is owned by a single background worker
    (_client_loop) so that __aenter__/__aexit__ run in the same asyncio
    task — required by anyio's cancel-scope rules.
    """

    CSS = """
    Screen {
        layout: vertical;
    }
    #main-area {
        height: 1fr;
    }
    #chat-log {
        width: 7fr;
        padding: 1 0 0 0;
    }
    #side-pane {
        width: 3fr;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel_or_quit", "Cancel/Quit", show=False),
        Binding("ctrl+d", "quit", "Quit", show=False),
        Binding("ctrl+l", "clear_log", "Clear", show=False),
        Binding("f2", "toggle_side_pane", "Side Pane", show=False),
    ]

    def __init__(self, options: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._options = options
        self._client: Any = None
        self._query_active = False
        self._pending_tools: dict[str, ToolCallGroup] = {}
        self._current_tool_group: ToolCallGroup | None = None
        self._queued_input: list[str] = []
        self._input_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._side_pane_visible: bool = True

    def compose(self) -> ComposeResult:
        workspace = getattr(self._options, "cwd", None) or os.getcwd()
        with Horizontal(id="main-area"):
            yield VerticalScroll(id="chat-log")
            with TabbedContent(id="side-pane"):
                yield TabPane(
                    "Tickets",
                    TicketPanel(workspace=workspace, id="ticket-tree"),
                    id="tab-tickets",
                )
                yield TabPane(
                    "Tools",
                    ToolLog(id="tool-log"),
                    id="tab-tools",
                )
                yield TabPane(
                    "Logs",
                    RichLog(id="log-pane", wrap=True, highlight=True),
                    id="tab-logs",
                )
        yield StatusBar(id="status-bar")
        yield QueueBar(id="queue-bar")
        yield PromptInput(id="prompt-input")

    async def on_mount(self) -> None:
        log_widget = self.query_one("#log-pane", RichLog)
        handler = TUILogHandler(log_widget)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        ))
        logging.getLogger("repo_tools").addHandler(handler)
        self._client_loop()
        self.query_one("#prompt-input", PromptInput).focus()

    # ── Client lifecycle worker ───────────────────────────────────────────

    @work(group="client")
    async def _client_loop(self) -> None:
        """Own the SDK client in a single task (enter + exit same task).

        Cancellation (Ctrl+C) works by starting a new ``_client_loop``
        worker.  Because ``group="client"``, Textual cancels this task
        first — the CancelledError breaks out of ``receive_response()``,
        the context-manager tears down the old client, and the new worker
        creates a fresh one.
        """
        from claude_agent_sdk import ClaudeSDKClient

        async with ClaudeSDKClient(options=self._options) as client:
            self._client = client
            try:
                while True:
                    user_input = await self._input_queue.get()
                    if user_input is _SENTINEL:
                        break
                    self._query_active = True
                    self.query_one("#status-bar", StatusBar).set_status(
                        "Working...", "working",
                    )
                    try:
                        await client.query(user_input)
                        async for msg in client.receive_response():
                            await self._handle_message(msg)
                    except asyncio.CancelledError:
                        raise  # let it propagate to tear down the client
                    except Exception:
                        logger.warning("Client query failed", exc_info=True)
                        self.query_one("#status-bar", StatusBar).set_status(
                            "Error", "error",
                        )
                    finally:
                        self._query_active = False
                        self._drain_queue()
            except asyncio.CancelledError:
                pass
        self._client = None

    # ── Input handling ────────────────────────────────────────────────────

    async def on_prompt_input_submitted(
        self, event: PromptInput.Submitted,
    ) -> None:
        text = event.text
        cmd = text.strip()

        if cmd in ("/exit", "/quit"):
            self._input_queue.put_nowait(_SENTINEL)
            self.exit()
            return

        if cmd.startswith("/"):
            chat_log = self.query_one("#chat-log", VerticalScroll)
            await chat_log.mount(Static(f"Unknown command: {cmd}"))
            return

        # Normal message
        chat_log = self.query_one("#chat-log", VerticalScroll)
        msg_widget = UserMessage(f"> {text}")
        await chat_log.mount(msg_widget)
        msg_widget.scroll_visible()

        if self._query_active:
            self._queued_input.append(text)
            self._refresh_queue_bar()
            return

        self._input_queue.put_nowait(text)

    def _drain_queue(self) -> None:
        """Send the next queued input, if any."""
        if self._queued_input:
            self._input_queue.put_nowait(self._queued_input.pop(0))
        self._refresh_queue_bar()

    def _refresh_queue_bar(self) -> None:
        """Sync QueueBar widget with current queue contents."""
        try:
            self.query_one("#queue-bar", QueueBar).refresh_queue(
                self._queued_input,
            )
        except Exception:
            logger.debug("Failed to refresh queue bar", exc_info=True)

    # ── Message handling ──────────────────────────────────────────────────

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
                    self._current_tool_group = None
                    widget = MarkdownMessage("")
                    widget.update(Markdown(block.text))
                    await chat_log.mount(widget)
                elif isinstance(block, ToolUseBlock):
                    # Compact group in chat
                    if self._current_tool_group is None:
                        self._current_tool_group = ToolCallGroup()
                        await chat_log.mount(self._current_tool_group)
                    self._current_tool_group.add_tool(
                        block.id, block.name,
                    )
                    self._pending_tools[block.id] = (
                        self._current_tool_group
                    )
                    # Rolling log in side pane
                    input_args = None
                    if hasattr(block, "input") and block.input:
                        input_args = block.input
                    try:
                        tool_log = self.query_one("#tool-log", ToolLog)
                        tool_log.add_tool(
                            block.id, block.name, input_args,
                        )
                        self.query_one(
                            "#side-pane", TabbedContent,
                        ).active = "tab-tools"
                    except Exception:
                        logger.debug("Failed to update tool log", exc_info=True)
                    self.query_one("#status-bar", StatusBar).set_status(
                        f"Working... ({block.name})", "working",
                    )
                elif isinstance(block, ToolResultBlock):
                    group = self._pending_tools.pop(
                        block.tool_use_id, None,
                    )
                    if group is not None:
                        group.set_result(
                            block.tool_use_id,
                            is_error=bool(block.is_error),
                        )
                    try:
                        tool_log = self.query_one("#tool-log", ToolLog)
                        tool_log.set_result(
                            block.tool_use_id,
                            str(block.content or ""),
                            is_error=bool(block.is_error),
                        )
                    except Exception:
                        logger.debug("Failed to update tool result", exc_info=True)
                elif isinstance(block, ThinkingBlock):
                    pass

        elif isinstance(msg, ResultMessage):
            parts = [f"Done ({msg.subtype})"]
            if msg.total_cost_usd is not None:
                parts.append(f"${msg.total_cost_usd:.4f}")
            parts.append(f"{msg.num_turns} turns")
            self.query_one("#status-bar", StatusBar).set_status(
                " \u2014 ".join(parts), "ready",
            )

        chat_log.scroll_end(animate=False)

    # ── Side pane ─────────────────────────────────────────────────────────

    def action_toggle_side_pane(self) -> None:
        """F2: toggle side pane visibility."""
        self._side_pane_visible = not self._side_pane_visible
        try:
            pane = self.query_one("#side-pane", TabbedContent)
            pane.display = self._side_pane_visible
        except Exception:
            logger.debug("Failed to toggle side pane", exc_info=True)

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated,
    ) -> None:
        """Refresh tickets when the Tickets tab is activated."""
        if event.pane.id == "tab-tickets":
            try:
                self.query_one("#ticket-tree", TicketPanel).refresh_tickets()
            except Exception:
                logger.debug("Failed to refresh tickets", exc_info=True)

    # ── Key bindings ──────────────────────────────────────────────────────

    def action_cancel_or_quit(self) -> None:
        if self._query_active:
            self._queued_input.clear()
            self._refresh_queue_bar()
            self.query_one("#status-bar", StatusBar).set_status(
                "Cancelled", "error",
            )
            # Restart the client worker — Textual cancels the old one
            # (group="client"), which raises CancelledError inside
            # receive_response(), tearing down the SDK session cleanly.
            self._client_loop()
        else:
            self._input_queue.put_nowait(_SENTINEL)
            self.exit()

    def action_clear_log(self) -> None:
        if not self._query_active:
            self.query_one("#chat-log", VerticalScroll).remove_children()
