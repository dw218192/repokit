"""Textual TUI for interactive agent sessions.

Tool calls appear as one-line status indicators in the chat log and as
collapsible entries in a rolling Tools side pane.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from rich.syntax import Syntax
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
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


# ── Windows subprocess console fix ────────────────────────────────────────────

_console_detach_applied = False


def _patch_subprocess_no_console() -> None:
    """Prevent ALL subprocesses from inheriting the TUI's console on Windows.

    Without this, child processes can briefly interact with the console,
    splitting SGR mouse escape sequences and injecting garbage like
    ``[<35;14;33M`` into the prompt.  Applies ``CREATE_NO_WINDOW`` to every
    ``subprocess.Popen`` call so no child can touch the parent console.

    This covers ``subprocess.run()``, ``asyncio.create_subprocess_exec()``,
    and ``anyio.open_process()`` — all of which use ``Popen`` internally.
    """
    global _console_detach_applied
    if _console_detach_applied:
        return
    _console_detach_applied = True

    import subprocess

    _original_popen_init = subprocess.Popen.__init__

    def _patched_popen_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("creationflags", 0)
        kwargs["creationflags"] |= subprocess.CREATE_NO_WINDOW
        _original_popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _patched_popen_init  # type: ignore[assignment]


# ── Widgets ───────────────────────────────────────────────────────────────────


class UserMessage(Static):
    """Submitted user prompt, styled distinctly."""

    DEFAULT_CSS = """
    UserMessage {
        margin: 1 1 0 1;
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
        margin: 1 1 0 1;
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
    StatusBar.status-planning { background: dodgerblue; }
    StatusBar.status-error { background: red; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("Ready", **kwargs)
        self.add_class("status-ready")

    def set_status(self, text: str, state: str = "ready") -> None:
        """Update text and color. state: ready, working, planning, or error."""
        self.update(text)
        self.remove_class("status-ready", "status-working", "status-planning", "status-error")
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


class TaskPanel(VerticalScroll):
    """Displays the current TodoWrite task list with status icons."""

    DEFAULT_CSS = """
    TaskPanel { height: 1fr; }
    TaskPanel .task-pending { color: $text-muted; }
    TaskPanel .task-in-progress { color: yellow; }
    TaskPanel .task-completed { color: green; }
    """

    _STATUS_ICONS: dict[str, str] = {
        "pending": "\u2610",
        "in_progress": "\u23f3",
        "completed": "\u2713",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._todos: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static("(no tasks)")

    def refresh_todos(self, todos: list[dict]) -> None:
        """Replace the displayed list with the latest state."""
        self._todos = todos
        self.remove_children()
        if not todos:
            self.mount(Static("(no tasks)"))
            return
        for item in todos:
            status = item.get("status", "pending")
            icon = self._STATUS_ICONS.get(status, "\u2610")
            if status == "in_progress":
                text = item.get("activeForm", item.get("content", ""))
            else:
                text = item.get("content", "")
            self.mount(Static(
                f"{icon} {text}",
                classes=f"task-{status.replace('_', '-')}",
            ))


class AvailableToolsPanel(VerticalScroll):
    """Displays registered tools grouped by category (Built-in, MCP)."""

    DEFAULT_CSS = """
    AvailableToolsPanel { height: 1fr; }
    AvailableToolsPanel .tool-name { margin-left: 2; padding: 0; }
    AvailableToolsPanel .tool-item { margin-left: 1; padding-top: 0; padding-bottom: 0; }
    AvailableToolsPanel .tool-desc { color: $text-muted; margin: 0 0 0 1; }
    """

    def __init__(self, tools: list[dict] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tools = tools or []

    def on_mount(self) -> None:
        self._populate()

    def _populate(self) -> None:
        self.remove_children()
        if not self._tools:
            self.mount(Static("(no tools registered)"))
            return

        groups: dict[str, list[dict]] = {}
        for t in self._tools:
            groups.setdefault(t.get("group", "Other"), []).append(t)

        for group_name, entries in groups.items():
            children: list[Static | Collapsible] = []
            for entry in entries:
                name = entry.get("name", "")
                desc = entry.get("description", "")
                if desc:
                    children.append(
                        Collapsible(
                            Static(desc, classes="tool-desc"),
                            title=name,
                            classes="tool-item",
                        )
                    )
                else:
                    children.append(
                        Static(f"[bold cyan]{name}[/bold cyan]", classes="tool-name")
                    )
            section = Collapsible(*children, title=f"{group_name} ({len(entries)})")
            self.mount(section)


def _summarize_todos(todos: list[dict]) -> str:
    """Compact progress string like ``3/5, Running tests``."""
    total = len(todos)
    completed = sum(1 for t in todos if t.get("status") == "completed")
    in_progress = [t for t in todos if t.get("status") == "in_progress"]
    parts = [f"{completed}/{total}"]
    if in_progress:
        text = in_progress[0].get("activeForm", "")
        if len(text) > 30:
            text = text[:27] + "..."
        if text:
            parts.append(text)
    return ", ".join(parts)


def _summarize_tool(name: str, input_args: dict | None) -> str:
    """One-line summary like ``Bash(cd /c/repo && ls)``."""
    if not input_args:
        return f"{name}()"
    if name == "Bash":
        arg = input_args.get("command", "")
    elif name in ("Read", "Edit", "Write"):
        arg = input_args.get("file_path", "")
    elif name in ("Glob", "Grep"):
        arg = input_args.get("pattern", "")
    elif name == "WebFetch":
        arg = input_args.get("url", "")
    elif name == "WebSearch":
        arg = input_args.get("query", "")
    elif name in ("Task", "Agent"):
        arg = input_args.get("description", "")
    elif name == "TodoWrite":
        todos = (input_args or {}).get("todos", [])
        if todos:
            return f"TodoWrite({_summarize_todos(todos)})"
        return "TodoWrite()"
    else:
        arg = next(
            (str(v) for v in input_args.values() if isinstance(v, str)),
            "...",
        )
    if len(arg) > 60:
        arg = arg[:57] + "..."
    return f"{name}({arg})"


_TODO_ICONS: dict[str, str] = {
    "pending": "\u2610",
    "in_progress": "\u23f3",
    "completed": "\u2713",
}


def _format_tool_body(name: str, input_args: dict | None) -> Any:
    """Format tool input as a Rich renderable (JSON syntax-highlighted)."""
    if not input_args:
        return "(no args)"
    if name == "TodoWrite":
        todos = input_args.get("todos", [])
        if todos:
            lines: list[str] = []
            for item in todos:
                status = item.get("status", "pending")
                icon = _TODO_ICONS.get(status, "\u2610")
                lines.append(f"{icon} {item.get('content', '')}")
            return "\n".join(lines)
    try:
        text = json.dumps(input_args, indent=2)[:500]
    except (TypeError, ValueError):
        text = str(input_args)[:500]
    return Syntax(text, "json", theme="ansi_dark", line_numbers=False)


class ChoicePanel(Static):
    """Renders AskUserQuestion choices for the user."""

    DEFAULT_CSS = """
    ChoicePanel {
        margin: 0 1;
        padding: 1;
        border: round $accent;
        background: $surface;
    }
    """

    def __init__(self, questions: list[dict], **kwargs: Any) -> None:
        lines: list[str] = []
        for q in questions:
            lines.append(f"[bold]{q.get('question', '')}[/bold]")
            for j, opt in enumerate(q.get("options", []), 1):
                label = opt.get("label", "")
                desc = opt.get("description", "")
                lines.append(
                    f"  {j}. {label}" + (f" — {desc}" if desc else ""),
                )
            lines.append("")
        super().__init__("\n".join(lines), markup=True, **kwargs)


def _parse_choice_answers(raw: str, questions: list[dict]) -> dict[str, str]:
    """Parse user input as numbered choices or free text.

    Multiple questions separated by semicolons.
    Numbers map to option labels; free text passed as-is.
    """
    parts = [p.strip() for p in raw.split(";")]
    answers: dict[str, str] = {}
    for i, q in enumerate(questions):
        q_text = q.get("question", f"q{i}")
        if i < len(parts):
            token = parts[i]
            try:
                idx = int(token) - 1
                options = q.get("options", [])
                if 0 <= idx < len(options):
                    answers[q_text] = options[idx].get("label", token)
                else:
                    answers[q_text] = token
            except ValueError:
                answers[q_text] = token
        else:
            answers[q_text] = ""
    return answers


class ToolLog(VerticalScroll):
    """Rolling log of tool calls with collapsible arg/output details."""

    DEFAULT_CSS = """
    ToolLog {
        height: 1fr;
    }
    ToolLog Collapsible { width: 100%; }
    ToolLog CollapsibleTitle { width: 100%; text-overflow: ellipsis; }
    ToolLog .tool-pending CollapsibleTitle { color: yellow; }
    ToolLog .tool-success CollapsibleTitle { color: green; }
    ToolLog .tool-error CollapsibleTitle { color: red; }
    ToolLog .tool-log-output { color: $text-muted; margin-top: 1; }
    ToolLog .tool-log-error { color: red; }
    ToolLog Collapsible Collapsible { margin-left: 2; }
    ToolLog Collapsible.-collapsed Collapsible { display: none; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # tool_id → (Collapsible, summary_text)
        self._entries: dict[str, tuple[Collapsible, str]] = {}
        self._output_widgets: dict[str, Static] = {}

    def on_mount(self) -> None:
        self.anchor()

    def add_tool(
        self, tool_id: str, name: str, input_args: dict | None,
        parent_id: str | None = None,
    ) -> None:
        """Register a new tool call as a collapsed collapsible entry."""
        summary = _summarize_tool(name, input_args)
        output_widget = Static("", classes="tool-log-output")
        output_widget.display = False
        entry = Collapsible(
            Static(_format_tool_body(name, input_args), classes="tool-log-body"),
            output_widget,
            title=f"\u23f3 {summary}",
            collapsed=True,
        )
        entry.add_class("tool-pending")
        self._entries[tool_id] = (entry, summary)
        self._output_widgets[tool_id] = output_widget
        # Nest inside parent if it exists
        parent_pair = self._entries.get(parent_id) if parent_id else None
        if parent_pair is not None:
            parent_pair[0].mount(entry)
        else:
            self.mount(entry)

    def set_result(
        self, tool_id: str, output: str, is_error: bool = False,
    ) -> None:
        """Update an entry with its result: set icon, show output."""
        pair = self._entries.get(tool_id)
        if pair is None:
            return
        entry, summary = pair
        icon = "\u2717" if is_error else "\u2713"
        entry.title = f"{icon} {summary}"
        entry.remove_class("tool-pending")
        entry.add_class("tool-error" if is_error else "tool-success")
        if output:
            output_widget = self._output_widgets.get(tool_id)
            if output_widget is not None:
                output_widget.update(output[:500])
                output_widget.display = True
                if is_error:
                    output_widget.add_class("tool-log-error")

    def clear(self) -> None:
        """Remove all entries and reset state."""
        self._entries.clear()
        self._output_widgets.clear()
        self.remove_children()


class PlanApprovalBar(Static):
    """Interactive Accept / Reject bar for plan review.

    Up/Down arrow keys toggle between "Accept" and the prompt input.
    Enter while this bar is focused → accept.  Typing in the prompt
    input and submitting → reject with that text.
    """

    DEFAULT_CSS = """
    PlanApprovalBar {
        height: 1;
        padding: 0 1;
        display: none;
    }
    PlanApprovalBar.plan-active {
        display: block;
        background: green 40%;
        color: $text;
    }
    PlanApprovalBar.plan-focused {
        background: green;
        color: white;
        text-style: bold;
    }
    """

    can_focus = True

    class Accepted(Message):
        pass

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Accepted())
        elif event.key == "down":
            event.stop()
            event.prevent_default()
            with contextlib.suppress(Exception):
                self.app.query_one("#prompt-input", PromptInput).focus()

    def on_focus(self) -> None:
        self.add_class("plan-focused")

    def on_blur(self) -> None:
        self.remove_class("plan-focused")


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

    class PopQueue(Message):
        """Posted when user presses Up on empty input to restore queued text."""

    async def _on_key(self, event: events.Key) -> None:
        r"""Intercept Enter before TextArea inserts a newline.

        ``\`` + Enter: remove the backslash and insert a real newline.
        Plain Enter: submit the accumulated text.
        Up on empty input: pop last queued message back into the prompt.
        Other keys: delegate to TextArea.
        """
        if event.key == "up":
            # If plan approval bar is active, arrow-up focuses it
            with contextlib.suppress(Exception):
                bar = self.app.query_one(
                    "#plan-approval", PlanApprovalBar,
                )
                if bar.has_class("plan-active"):
                    event.stop()
                    event.prevent_default()
                    bar.focus()
                    return
            # On empty input, pop the queue
            if not self.text.strip():
                event.stop()
                event.prevent_default()
                self.post_message(self.PopQueue())
                return
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
        with contextlib.suppress(Exception):
            self._widget.write(self.format(record))


# ── Chat history persistence ──────────────────────────────────────────────────


class ChatHistory:
    """Persist and replay chat messages across session resumes.

    Messages are stored as JSONL in ``_agent/sessions/<session_id>.jsonl``.
    """

    def __init__(self, workspace: str | None = None) -> None:
        self._workspace = Path(workspace or os.getcwd())
        self._session_id: str | None = None
        self._path: Path | None = None
        self._buffer: list[dict[str, str]] = []

    def bind(self, session_id: str) -> None:
        """Bind to a session — opens the log file for appending."""
        self._session_id = session_id
        sessions_dir = self._workspace / "_agent" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._path = sessions_dir / f"{session_id}.jsonl"
        # Flush any messages that arrived before bind()
        if self._buffer:
            try:
                with self._path.open("a", encoding="utf-8") as f:
                    for entry in self._buffer:
                        f.write(json.dumps(entry) + "\n")
            except OSError:
                logger.warning("Failed to flush chat history buffer", exc_info=True)
            self._buffer.clear()

    def append(self, role: str, text: str) -> None:
        """Append a message to the log."""
        entry = {"role": role, "text": text}
        if self._path is None:
            self._buffer.append(entry)
            return
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            logger.warning("Failed to write chat history", exc_info=True)

    @staticmethod
    def load(workspace: str | Path, session_id: str) -> list[dict[str, str]]:
        """Load previous chat messages for a session."""
        path = Path(workspace) / "_agent" / "sessions" / f"{session_id}.jsonl"
        if not path.exists():
            return []
        entries: list[dict[str, str]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to load chat history", exc_info=True)
        return entries


# ── App ───────────────────────────────────────────────────────────────────────


_SENTINEL = object()
_PLAN_ACCEPTED = object()


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
    #side-container {
        width: 3fr;
    }
    #side-pane {
        width: 1fr;
    }
    #wrap-toggle {
        height: 1;
        dock: bottom;
        text-align: right;
        color: $text-muted;
    }
    .hscroll {
        overflow-x: auto !important;
    }
    .hscroll > * {
        width: auto;
    }
    .hscroll Static {
        width: auto;
    }
    .hscroll Collapsible {
        width: auto;
    }
    .history {
        opacity: 60%;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel_or_quit", "Cancel/Quit", show=False),
        Binding("ctrl+d", "quit", "Quit", show=False),
        Binding("ctrl+l", "clear_log", "Clear", show=False),
        Binding("f2", "toggle_side_pane", "Side Pane", show=False),
        Binding("f3", "toggle_wrap", "Wrap/Scroll", show=False),
    ]

    def __init__(
        self,
        options: Any,
        initial_prompt: str | None = None,
        resume: str | None = None,
        tools_metadata: list[dict[str, str]] | None = None,
        human_ticket_review: bool = False,
        required_criteria: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._options = options
        self._initial_prompt = initial_prompt
        self._resume = resume
        self._tools_metadata = tools_metadata or []
        self._human_ticket_review = human_ticket_review
        self._required_criteria: list[str] = required_criteria or []
        self._session_id: str | None = None
        self._chat_history = ChatHistory(
            workspace=options.cwd,
        )
        self._client: Any = None
        self._query_active = False
        self._pending_tools: dict[str, ToolCallGroup] = {}
        self._current_tool_group: ToolCallGroup | None = None
        self._queued_input: list[str] = []
        self._input_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._todos: list[dict] = []
        self._side_pane_visible: bool = True
        self._side_wrap: bool = True
        self._choice_future: asyncio.Future | None = None
        self._ticket_fingerprint: tuple[int, float] = (0, 0.0)
        self._choice_questions: list[dict] | None = None

    def compose(self) -> ComposeResult:
        workspace = self._options.cwd or os.getcwd()
        with Horizontal(id="main-area"):
            yield VerticalScroll(id="chat-log")
            with Vertical(id="side-container"):
                with TabbedContent(id="side-pane"):
                    yield TabPane(
                        "Tickets",
                        TicketPanel(workspace=workspace, id="ticket-tree"),
                        id="tab-tickets",
                    )
                    yield TabPane(
                        "Tasks",
                        TaskPanel(id="task-panel"),
                        id="tab-tasks",
                    )
                    yield TabPane(
                        "Tools",
                        AvailableToolsPanel(
                            tools=self._tools_metadata,
                            id="available-tools",
                        ),
                        id="tab-available",
                    )
                    yield TabPane(
                        "Tool Log",
                        ToolLog(id="tool-log"),
                        id="tab-tools",
                    )
                    yield TabPane(
                        "Logs",
                        RichLog(id="log-pane", wrap=True, highlight=True),
                        id="tab-logs",
                    )
                yield Static("[F3 Wrap]", id="wrap-toggle")
        yield StatusBar(id="status-bar")
        yield QueueBar(id="queue-bar")
        yield PlanApprovalBar(
            "\u2714 Accept plan  (Enter)",
            id="plan-approval",
        )
        yield PromptInput(id="prompt-input")

    async def on_mount(self) -> None:
        log_widget = self.query_one("#log-pane", RichLog)
        handler = TUILogHandler(log_widget)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        ))
        repo_logger = logging.getLogger("repo_tools")
        repo_logger.addHandler(handler)
        repo_logger.propagate = False

        # Replay previous conversation when resuming
        if self._resume:
            workspace = self._options.cwd or os.getcwd()
            await self._replay_history(workspace, self._resume)

        if sys.platform == "win32":
            _patch_subprocess_no_console()
        self._install_tui_hooks()
        self._client_loop()
        self.query_one("#prompt-input", PromptInput).focus()
        if self._initial_prompt:
            asyncio.ensure_future(self._send_input(self._initial_prompt))

    async def _replay_history(self, workspace: str, session_id: str) -> None:
        """Mount previous conversation messages from the chat log file."""
        entries = ChatHistory.load(workspace, session_id)
        if not entries:
            return
        chat_log = self.query_one("#chat-log", VerticalScroll)
        await chat_log.mount(Static(
            f"[dim]--- resumed session ({len(entries)} messages) ---[/dim]",
            markup=True,
        ))
        for entry in entries:
            role = entry.get("role", "")
            text = entry.get("text", "")
            if role == "user":
                widget = UserMessage(f"> {text}")
                widget.add_class("history")
            else:
                widget = MarkdownMessage(Markdown(text))
                widget.add_class("history")
            await chat_log.mount(widget)
        await chat_log.mount(Static(
            "[dim]--- end of history ---[/dim]", markup=True,
        ))

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

        if self._resume:
            self._options.resume = self._resume
            self._resume = None  # only use once

        try:
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
                            # Auto-exit when an event subscription is pending
                            from .events import has_subscriptions
                            if has_subscriptions():
                                logger.info("Event subscription pending — suspending session")
                                self.exit()
                                return
                        except asyncio.CancelledError:
                            raise  # let it propagate to tear down the client
                        except Exception as exc:
                            logger.error("Client query failed", exc_info=True)
                            await self._show_error(str(exc))
                        finally:
                            self._query_active = False
                            self._drain_queue()
                except asyncio.CancelledError:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("SDK client crashed", exc_info=True)
            await self._show_error(f"SDK client crashed: {exc}")
        self._client = None

    # ── Input handling ────────────────────────────────────────────────────

    async def on_prompt_input_submitted(
        self, event: PromptInput.Submitted,
    ) -> None:
        text = event.text

        # Resolve pending choice (ExitPlanMode / AskUserQuestion)
        if self._choice_future is not None and not self._choice_future.done():
            chat_log = self.query_one("#chat-log", VerticalScroll)
            msg_widget = UserMessage(f"> {text}")
            await chat_log.mount(msg_widget)
            msg_widget.scroll_visible()
            self._choice_future.set_result(text)
            return

        cmd = text.strip()

        if cmd in ("/exit", "/quit"):
            self._input_queue.put_nowait(_SENTINEL)
            self.exit()
            return

        if cmd == "/clear":
            self._queued_input.clear()
            self._refresh_queue_bar()
            self._pending_tools.clear()
            self._current_tool_group = None
            self._todos = []
            self.query_one("#chat-log", VerticalScroll).remove_children()
            self.query_one("#tool-log", ToolLog).clear()
            with contextlib.suppress(Exception):
                self.query_one("#task-panel", TaskPanel).refresh_todos([])
            self.query_one("#status-bar", StatusBar).set_status(
                "Ready", "ready",
            )
            self._client_loop()
            return

        if cmd.startswith("/"):
            chat_log = self.query_one("#chat-log", VerticalScroll)
            await chat_log.mount(Static(f"Unknown command: {cmd}"))
            return

        # Normal message — only show in chat when actually sent
        if self._query_active:
            self._queued_input.append(text)
            self._refresh_queue_bar()
            return

        await self._send_input(text)

    async def _send_input(self, text: str) -> None:
        """Display a user message in chat and put it on the input queue."""
        chat_log = self.query_one("#chat-log", VerticalScroll)
        msg_widget = UserMessage(f"> {text}")
        await chat_log.mount(msg_widget)
        msg_widget.scroll_visible()
        self._chat_history.append("user", text)
        self._input_queue.put_nowait(text)

    def on_prompt_input_pop_queue(
        self, event: PromptInput.PopQueue,
    ) -> None:
        """Up arrow on empty input: pop last queued message back to prompt."""
        if self._queued_input:
            restored = self._queued_input.pop()
            self._refresh_queue_bar()
            pi = self.query_one("#prompt-input", PromptInput)
            pi.load_text(restored)

    async def _show_error(self, message: str) -> None:
        """Display an error message in the chat log and update status bar."""
        chat_log = self.query_one("#chat-log", VerticalScroll)
        await chat_log.mount(
            Static(f"[bold red]Error:[/] {message}", classes="error-msg"),
        )
        chat_log.scroll_end(animate=False)
        self.query_one("#status-bar", StatusBar).set_status("Error", "error")

    def _drain_queue(self) -> None:
        """Send the next queued input, if any."""
        if self._queued_input:
            text = self._queued_input.pop(0)
            # Schedule _send_input so the message appears in chat when sent
            asyncio.ensure_future(self._send_input(text))
        self._refresh_queue_bar()

    def _refresh_queue_bar(self) -> None:
        """Sync QueueBar widget with current queue contents."""
        try:
            self.query_one("#queue-bar", QueueBar).refresh_queue(
                self._queued_input,
            )
        except Exception:
            logger.warning("Failed to refresh queue bar", exc_info=True)

    # ── Message handling ──────────────────────────────────────────────────

    async def _handle_message(self, msg: Any) -> None:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            UserMessage as SdkUserMessage,
        )
        from claude_agent_sdk.types import (
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
        )

        chat_log = self.query_one("#chat-log", VerticalScroll)

        if isinstance(msg, AssistantMessage):
            parent_id = getattr(msg, "parent_tool_use_id", None)
            for block in msg.content:
                if isinstance(block, TextBlock):
                    self._current_tool_group = None
                    await chat_log.mount(
                        MarkdownMessage(Markdown(block.text)),
                    )
                    self._chat_history.append("assistant", block.text)
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
                            parent_id=parent_id,
                        )
                    except Exception:
                        logger.warning("Failed to update tool log", exc_info=True)
                    # EnterPlanMode — switch status to planning
                    if block.name == "EnterPlanMode":
                        self.query_one("#status-bar", StatusBar).set_status(
                            "Planning...", "planning",
                        )
                        return
                    # TodoWrite — update task panel and status bar
                    if block.name == "TodoWrite" and input_args:
                        todos = input_args.get("todos", [])
                        self._todos = todos
                        try:
                            self.query_one("#task-panel", TaskPanel).refresh_todos(todos)
                        except Exception:
                            logger.warning("Failed to update task panel", exc_info=True)
                        in_progress = [t for t in todos if t.get("status") == "in_progress"]
                        if in_progress:
                            active = in_progress[0].get("activeForm", "")
                            if active:
                                self.query_one("#status-bar", StatusBar).set_status(
                                    f"\u23f3 {active}", "working",
                                )
                                return
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
                        logger.warning("Failed to update tool result", exc_info=True)
                    self._maybe_refresh_tickets()
                elif isinstance(block, ThinkingBlock):
                    pass

        elif isinstance(msg, SdkUserMessage):
            # SDK sends ToolResultBlocks inside UserMessage, not AssistantMessage
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
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
                            logger.warning("Failed to update tool result", exc_info=True)
                        self._maybe_refresh_tickets()

        elif isinstance(msg, ResultMessage):
            # Capture session_id for event-loop resume + chat history
            self._session_id = msg.session_id
            self._chat_history.bind(msg.session_id)

            # Flush any remaining pending tools (edge cases)
            for tool_id, group in self._pending_tools.items():
                group.set_result(tool_id, is_error=False)
                try:
                    tool_log = self.query_one("#tool-log", ToolLog)
                    tool_log.set_result(tool_id, "")
                except Exception:
                    logger.warning("Failed to flush tool result", exc_info=True)
            self._pending_tools.clear()

            parts = [f"Done ({msg.subtype})"]
            if msg.total_cost_usd is not None:
                parts.append(f"${msg.total_cost_usd:.4f}")
            parts.append(f"{msg.num_turns} turns")
            self.query_one("#status-bar", StatusBar).set_status(
                " \u2014 ".join(parts), "ready",
            )

        chat_log.scroll_end(animate=False)

    # ── PreToolUse hooks (plan approval + user questions) ───────────────

    def _open_choice_future(self) -> None:
        """Create the input future immediately so no user input is lost.

        Must be called **before** any async work (widget mounts, file I/O)
        that precedes the actual ``await``.
        """
        if self._choice_future is None or self._choice_future.done():
            loop = asyncio.get_event_loop()
            self._choice_future = loop.create_future()

    async def _await_choice_future(self) -> str:
        """Await and clean up the choice future.

        Raises asyncio.CancelledError if interrupted (e.g. Ctrl+C).
        Requires ``_open_choice_future()`` to have been called first.
        """
        assert self._choice_future is not None
        try:
            return await self._choice_future
        finally:
            self._choice_future = None

    def _install_tui_hooks(self) -> None:
        """Register PreToolUse hooks so the TUI intercepts ExitPlanMode
        and AskUserQuestion.  These fire regardless of permission_mode
        (unlike can_use_tool which is skipped under bypassPermissions).
        """
        from claude_agent_sdk import HookMatcher

        hooks = self._options.hooks
        if hooks is None:
            hooks = {}
            self._options.hooks = hooks

        pre_tool_use = hooks.setdefault("PreToolUse", [])
        pre_tool_use.append(HookMatcher(
            matcher="ExitPlanMode",
            hooks=[self._exit_plan_mode_hook],
        ))
        pre_tool_use.append(HookMatcher(
            matcher="AskUserQuestion",
            hooks=[self._ask_user_question_hook],
        ))
        if self._human_ticket_review:
            pre_tool_use.append(HookMatcher(
                matcher="mcp__repokit-agent__create_ticket",
                hooks=[self._approve_ticket_hook],
            ))

    async def _exit_plan_mode_hook(
        self, input_data: dict[str, Any],
        tool_use_id: str | None, context: Any,
    ) -> dict[str, Any]:
        """PreToolUse hook for ExitPlanMode — shows the plan approval bar."""
        try:
            self._open_choice_future()

            chat_log = self.query_one("#chat-log", VerticalScroll)

            # Find and display the most recent plan file.
            cwd = Path(self._options.cwd or os.getcwd())
            search_dirs = [
                cwd / "_agent" / "plans",
                Path.home() / ".claude" / "plans",
            ]
            content = None
            for plans_dir in search_dirs:
                if plans_dir.is_dir():
                    plan_files = sorted(
                        plans_dir.glob("*.md"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    if plan_files:
                        try:
                            content = plan_files[0].read_text(encoding="utf-8")
                            break
                        except OSError as exc:
                            logger.warning(
                                "Failed to read plan file %s: %s",
                                plan_files[0], exc,
                            )
                            continue
            if content:
                await chat_log.mount(MarkdownMessage(Markdown(content)))

            bar = self.query_one("#plan-approval", PlanApprovalBar)
            bar.add_class("plan-active")
            bar.focus()
            self.query_one("#status-bar", StatusBar).set_status(
                "Awaiting plan approval...", "planning",
            )

            answer = await self._await_choice_future()

            bar.remove_class("plan-active", "plan-focused")
            self.query_one("#prompt-input", PromptInput).focus()
            self.query_one("#status-bar", StatusBar).set_status(
                "Working...", "working",
            )

            if answer is _PLAN_ACCEPTED:
                return {}
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": answer,
                },
            }
        except asyncio.CancelledError:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "interrupted",
                },
            }

    async def _ask_user_question_hook(
        self, input_data: dict[str, Any],
        tool_use_id: str | None, context: Any,
    ) -> dict[str, Any]:
        """PreToolUse hook for AskUserQuestion — shows the choice panel.

        Collects user answers via the TUI, then allows the tool to proceed
        with ``updatedInput`` containing pre-filled ``answers``.  The CLI
        sees the answers already populated and skips interactive prompting
        (which would hang because stdin/stdout are piped).
        """
        try:
            tool_input = input_data.get("tool_input", {})
            questions = tool_input.get("questions", [])
            if not questions:
                return {}

            self._open_choice_future()
            self._choice_questions = questions

            chat_log = self.query_one("#chat-log", VerticalScroll)
            panel = ChoicePanel(questions)
            await chat_log.mount(panel)
            chat_log.scroll_end(animate=False)
            self.query_one("#prompt-input", PromptInput).focus()

            answer_text = await self._await_choice_future()
            answers = _parse_choice_answers(answer_text, questions)
            self._choice_questions = None

            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": {**tool_input, "answers": answers},
                },
            }
        except asyncio.CancelledError:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "interrupted",
                },
            }

    async def _approve_ticket_hook(
        self, input_data: dict[str, Any],
        tool_use_id: str | None, context: Any,
    ) -> dict[str, Any]:
        """PreToolUse hook for create_ticket — asks user to approve."""
        try:
            tool_input = input_data.get("tool_input", {})
            title = tool_input.get("title", "Untitled")
            ticket_id = tool_input.get("id", "")
            desc = tool_input.get("description", "")
            user_criteria = list(tool_input.get("criteria") or [])

            # Merge project-required criteria (same dedup logic as tickets.py)
            seen = set(user_criteria)
            for rc in self._required_criteria:
                if rc not in seen:
                    user_criteria.append(rc)
                    seen.add(rc)
            criteria = user_criteria

            # Build a readable summary as the question body.
            parts = [f"[bold]{ticket_id}[/bold] — {title}"]
            if desc:
                # Show first ~200 chars of description to keep it concise.
                summary = desc[:200] + ("..." if len(desc) > 200 else "")
                parts.append(f"\n{summary}")
            if criteria:
                parts.append("\n[bold]Acceptance criteria:[/bold]")
                for c in criteria:
                    parts.append(f"  \u2022 {c}")

            question_text = "\n".join(parts)
            questions = [{
                "question": question_text,
                "header": "New ticket",
                "options": [
                    {
                        "label": "Create ticket",
                        "description": "Approve and create this ticket",
                    },
                    {
                        "label": "Skip",
                        "description": "Do not create this ticket",
                    },
                ],
                "multiSelect": False,
            }]

            self._open_choice_future()
            self._choice_questions = questions

            chat_log = self.query_one("#chat-log", VerticalScroll)
            panel = ChoicePanel(questions)
            await chat_log.mount(panel)
            chat_log.scroll_end(animate=False)
            self.query_one("#prompt-input", PromptInput).focus()

            answer_text = await self._await_choice_future()
            answers = _parse_choice_answers(answer_text, questions)
            self._choice_questions = None

            chosen = answers.get(question_text, "")
            if chosen == "Create ticket":
                return {}
            reason = chosen if chosen and chosen != "Skip" else f"User skipped ticket '{ticket_id}'."
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                },
            }

        except asyncio.CancelledError:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "interrupted",
                },
            }

    async def on_plan_approval_bar_accepted(
        self, event: PlanApprovalBar.Accepted,
    ) -> None:
        """User pressed Enter on the Accept bar."""
        if self._choice_future and not self._choice_future.done():
            self._choice_future.set_result(_PLAN_ACCEPTED)

    # ── Side pane ─────────────────────────────────────────────────────────

    def action_toggle_side_pane(self) -> None:
        """F2: toggle side pane visibility."""
        self._side_pane_visible = not self._side_pane_visible
        try:
            container = self.query_one("#side-container", Vertical)
            container.display = self._side_pane_visible
        except Exception:
            logger.warning("Failed to toggle side pane", exc_info=True)

    def action_toggle_wrap(self) -> None:
        """F3: toggle between wrap and horizontal-scroll modes."""
        self._side_wrap = not self._side_wrap
        for panel_id in (
            "#ticket-tree", "#task-panel", "#available-tools", "#tool-log",
        ):
            with contextlib.suppress(Exception):
                panel = self.query_one(panel_id, VerticalScroll)
                if self._side_wrap:
                    panel.remove_class("hscroll")
                else:
                    panel.add_class("hscroll")
        with contextlib.suppress(Exception):
            log_pane = self.query_one("#log-pane", RichLog)
            log_pane.wrap = self._side_wrap
        with contextlib.suppress(Exception):
            toggle = self.query_one("#wrap-toggle", Static)
            toggle.update("[F3 Wrap]" if self._side_wrap else "[F3 Scroll]")

    @on(events.Click, "#wrap-toggle")
    def _on_wrap_toggle_click(self) -> None:
        self.action_toggle_wrap()

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated,
    ) -> None:
        """Refresh tickets when the Tickets tab is activated."""
        if event.pane.id == "tab-tickets":
            try:
                self.query_one("#ticket-tree", TicketPanel).refresh_tickets()
            except Exception:
                logger.warning("Failed to refresh tickets", exc_info=True)

    def _maybe_refresh_tickets(self) -> None:
        """Refresh ticket panel if any ticket file changed on disk."""
        workspace = self._options.cwd or os.getcwd()
        ticket_dir = Path(workspace) / "_agent" / "tickets"
        try:
            files = list(ticket_dir.glob("*.json"))
            mtime = max((f.stat().st_mtime for f in files), default=0.0)
            fp = (len(files), mtime)
        except (FileNotFoundError, OSError):
            fp = (0, 0.0)
        if fp != self._ticket_fingerprint:
            self._ticket_fingerprint = fp
            try:
                self.query_one("#ticket-tree", TicketPanel).refresh_tickets()
            except Exception:
                logger.warning("Failed to refresh tickets", exc_info=True)

    # ── Key bindings ──────────────────────────────────────────────────────

    def action_cancel_or_quit(self) -> None:
        if self._query_active:
            self._queued_input.clear()
            self._refresh_queue_bar()
            self.query_one("#status-bar", StatusBar).set_status(
                "Cancelled", "error",
            )
            # Cancel any pending user-input Future (plan approval,
            # AskUserQuestion) so the PreToolUse hook unblocks immediately.
            if self._choice_future and not self._choice_future.done():
                self._choice_future.cancel()
            # Send interrupt through the SDK transport so the CLI
            # subprocess actually stops its current turn.
            if self._client is not None:
                asyncio.ensure_future(self._client.interrupt())
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
