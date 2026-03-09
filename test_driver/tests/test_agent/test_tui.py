"""Tests for the Textual TUI — QueueBar, PromptInput, ToolCallGroup, ToolLog, side pane."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from textual.widgets import Collapsible

from rich.syntax import Syntax

from repo_tools.agent.tui import (
    AgentApp,
    AvailableToolsPanel,
    ChoicePanel,
    MarkdownMessage,
    PlanApprovalBar,
    PromptInput,
    QueueBar,
    StatusBar,
    TaskPanel,
    TicketPanel,
    ToolCallGroup,
    ToolLog,
    TUILogHandler,
    UserMessage,
    _PLAN_ACCEPTED,
    _format_tool_body,
    _parse_choice_answers,
    _summarize_todos,
    _summarize_tool,
    _ticket_to_markdown,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(coro):
    """Run an async test without pytest-asyncio."""
    asyncio.run(coro())


def _make_mock_options():
    """Build mock ClaudeAgentOptions."""
    opts = MagicMock()
    opts.cwd = None
    return opts


# ── QueueBar widget tests ────────────────────────────────────────────────────


class TestQueueBar:
    def test_hidden_on_mount(self):
        """QueueBar starts with zero height after mount."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield QueueBar(id="q")

            app = _App()
            async with app.run_test() as pilot:
                qb = app.query_one("#q", QueueBar)
                await pilot.pause()
                assert qb.size.height == 0

        _run(_test)

    def test_visible_after_refresh_with_items(self):
        """QueueBar gets nonzero height when refresh_queue receives items."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield QueueBar(id="q")

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                qb = app.query_one("#q", QueueBar)
                qb.refresh_queue(["hello", "world"])
                await pilot.pause()
                assert qb.size.height > 0

        _run(_test)

    def test_hidden_after_refresh_empty(self):
        """QueueBar collapses to zero height when items cleared."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield QueueBar(id="q")

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                qb = app.query_one("#q", QueueBar)
                qb.refresh_queue(["hello"])
                await pilot.pause()
                assert qb.size.height > 0

                qb.refresh_queue([])
                await pilot.pause()
                assert qb.size.height == 0

        _run(_test)

    def test_content_shows_numbered_items(self):
        """QueueBar content has numbered items."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield QueueBar(id="q")

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                qb = app.query_one("#q", QueueBar)
                qb.refresh_queue(["fix bug", "add tests"])
                await pilot.pause()
                rendered = str(qb.render())
                assert "[1]" in rendered
                assert "[2]" in rendered
                assert "fix bug" in rendered

        _run(_test)

    def test_visible_in_full_layout(self):
        """QueueBar becomes visible in a layout matching AgentApp."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.containers import VerticalScroll

            class _App(App):
                CSS = """
                #chat-log { height: 1fr; }
                """

                def compose(self) -> ComposeResult:
                    yield VerticalScroll(id="chat-log")
                    yield StatusBar(id="status-bar")
                    yield QueueBar(id="queue-bar")
                    yield PromptInput(id="prompt-input")

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                qb = app.query_one("#queue-bar", QueueBar)

                # Initially collapsed
                await pilot.pause()
                assert qb.size.height == 0

                # Show queue
                qb.refresh_queue(["queued msg 1"])
                await pilot.pause()
                assert qb.size.height > 0

                # Hide again
                qb.refresh_queue([])
                await pilot.pause()
                assert qb.size.height == 0

        _run(_test)


# ── ToolCallGroup tests ──────────────────────────────────────────────────────


class TestToolCallGroup:
    def test_add_tool_shows_running_icon(self):
        """add_tool renders with \u25b8 prefix."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolCallGroup(id="tg")

            app = _App()
            async with app.run_test() as pilot:
                tg = app.query_one("#tg", ToolCallGroup)
                tg.add_tool("t1", "Read")
                await pilot.pause()
                rendered = str(tg.render())
                assert "\u25b8" in rendered
                assert "Read" in rendered

        _run(_test)

    def test_set_result_success(self):
        """\u2713 icon on success."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolCallGroup(id="tg")

            app = _App()
            async with app.run_test() as pilot:
                tg = app.query_one("#tg", ToolCallGroup)
                tg.add_tool("t1", "Read")
                tg.set_result("t1")
                await pilot.pause()
                rendered = str(tg.render())
                assert "\u2713" in rendered
                assert "Read" in rendered

        _run(_test)

    def test_set_result_error(self):
        """\u2717 icon on error."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolCallGroup(id="tg")

            app = _App()
            async with app.run_test() as pilot:
                tg = app.query_one("#tg", ToolCallGroup)
                tg.add_tool("t1", "Bash")
                tg.set_result("t1", is_error=True)
                await pilot.pause()
                rendered = str(tg.render())
                assert "\u2717" in rendered
                assert "Bash" in rendered

        _run(_test)

    def test_groups_consecutive_tools(self):
        """Multiple tools render on one line."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolCallGroup(id="tg")

            app = _App()
            async with app.run_test() as pilot:
                tg = app.query_one("#tg", ToolCallGroup)
                tg.add_tool("t1", "Read")
                tg.add_tool("t2", "Edit")
                tg.set_result("t1")
                await pilot.pause()
                rendered = str(tg.render())
                assert "Read" in rendered
                assert "Edit" in rendered
                assert "\u2713" in rendered
                assert "\u25b8" in rendered

        _run(_test)


# ── PromptInput tests ────────────────────────────────────────────────────────


class TestPromptInput:
    def test_enter_submits(self):
        """Enter key submits the text and clears the input."""

        async def _test():
            from textual.app import App, ComposeResult

            submitted = []

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield PromptInput(id="pi")

                def on_prompt_input_submitted(self, event):
                    submitted.append(event.text)

            app = _App()
            async with app.run_test() as pilot:
                pi = app.query_one("#pi", PromptInput)
                pi.focus()
                # Type text then press enter
                for ch in "hello":
                    await pilot.press(ch)
                await pilot.press("enter")
                await pilot.pause()
                assert submitted == ["hello"]
                assert pi.text == ""

        _run(_test)

    def test_backslash_enter_inserts_newline(self):
        r"""\ + Enter removes backslash and inserts newline."""

        async def _test():
            from textual.app import App, ComposeResult

            submitted = []

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield PromptInput(id="pi")

                def on_prompt_input_submitted(self, event):
                    submitted.append(event.text)

            app = _App()
            async with app.run_test() as pilot:
                pi = app.query_one("#pi", PromptInput)
                pi.focus()
                for ch in "line1\\":
                    await pilot.press(ch)
                await pilot.press("enter")
                await pilot.pause()
                # Should NOT have submitted — should have inserted newline
                assert submitted == []
                assert "\n" in pi.text
                assert "\\" not in pi.text

        _run(_test)

    def test_empty_enter_does_nothing(self):
        """Enter on empty input does not submit."""

        async def _test():
            from textual.app import App, ComposeResult

            submitted = []

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield PromptInput(id="pi")

                def on_prompt_input_submitted(self, event):
                    submitted.append(event.text)

            app = _App()
            async with app.run_test() as pilot:
                pi = app.query_one("#pi", PromptInput)
                pi.focus()
                await pilot.press("enter")
                await pilot.pause()
                assert submitted == []

        _run(_test)


# ── AgentApp queue integration tests ─────────────────────────────────────────


def _make_test_app_class():
    """Build a minimal AgentApp-like class with no SDK dependency.

    Reproduces the exact same _messages / queue logic as AgentApp.
    """
    from textual.app import App, ComposeResult
    from textual.binding import Binding as _Binding
    from textual.containers import VerticalScroll

    class _TestApp(App):
        CSS = """
        #chat-log { height: 1fr; padding: 1 0 0 0; }
        """

        BINDINGS = [
            _Binding("escape", "noop", show=False),
        ]

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._messages: list[str] = []

        @property
        def busy(self) -> bool:
            return len(self._messages) > 0

        def compose(self) -> ComposeResult:
            yield VerticalScroll(id="chat-log")
            yield StatusBar(id="status-bar")
            yield QueueBar(id="queue-bar")
            yield PromptInput(id="prompt-input")

        def on_prompt_input_submitted(self, event):
            text = event.text
            self._messages.append(text)
            if len(self._messages) == 1:
                chat_log = self.query_one("#chat-log", VerticalScroll)
                chat_log.mount(UserMessage(f"> {text}"))
            self._refresh_queue_bar()

        def _refresh_queue_bar(self):
            self.query_one("#queue-bar", QueueBar).refresh_queue(
                self._messages[1:],
            )

        def finish_active(self):
            """Simulate worker completing the active query."""
            if self._messages:
                self._messages.pop(0)
            self._refresh_queue_bar()

        def action_noop(self):
            pass

    return _TestApp


class TestAgentAppQueue:
    def test_first_submit_is_active(self):
        """First message becomes active (not queued)."""

        async def _test():
            App = _make_test_app_class()
            app = App()
            async with app.run_test(size=(80, 24)) as pilot:
                pi = app.query_one("#prompt-input", PromptInput)
                pi.focus()

                for ch in "first":
                    await pilot.press(ch)
                await pilot.press("enter")
                await pilot.pause()

                assert len(app._messages) == 1
                assert app.busy is True
                # QueueBar should NOT show (no pending items)
                qb = app.query_one("#queue-bar", QueueBar)
                assert qb.size.height == 0

        _run(_test)

    def test_second_submit_goes_to_queue(self):
        """Second message while busy goes to QueueBar."""

        async def _test():
            App = _make_test_app_class()
            app = App()
            async with app.run_test(size=(80, 24)) as pilot:
                pi = app.query_one("#prompt-input", PromptInput)
                qb = app.query_one("#queue-bar", QueueBar)
                pi.focus()

                # Submit first
                for ch in "first":
                    await pilot.press(ch)
                await pilot.press("enter")
                await pilot.pause()

                # Submit second (busy — should queue)
                for ch in "second":
                    await pilot.press(ch)
                await pilot.press("enter")
                await pilot.pause()

                assert len(app._messages) == 2
                assert app._messages == ["first", "second"]
                assert qb.size.height > 0, "QueueBar should show queued item"

        _run(_test)

    def test_third_submit_shows_two_queued(self):
        """Three rapid submits: 1 active + 2 queued."""

        async def _test():
            App = _make_test_app_class()
            app = App()
            async with app.run_test(size=(80, 24)) as pilot:
                pi = app.query_one("#prompt-input", PromptInput)
                qb = app.query_one("#queue-bar", QueueBar)
                pi.focus()

                for msg in ["a", "b", "c"]:
                    for ch in msg:
                        await pilot.press(ch)
                    await pilot.press("enter")
                    await pilot.pause()

                assert len(app._messages) == 3
                rendered = str(qb.render())
                assert "Queued (2)" in rendered

        _run(_test)

    def test_finish_active_drains_queue(self):
        """Finishing the active message removes it and hides QueueBar."""

        async def _test():
            App = _make_test_app_class()
            app = App()
            async with app.run_test(size=(80, 24)) as pilot:
                pi = app.query_one("#prompt-input", PromptInput)
                qb = app.query_one("#queue-bar", QueueBar)
                pi.focus()

                # Submit two messages
                for msg in ["first", "second"]:
                    for ch in msg:
                        await pilot.press(ch)
                    await pilot.press("enter")
                    await pilot.pause()

                assert qb.size.height > 0

                # Simulate worker completing first query
                app.finish_active()
                await pilot.pause()

                # "second" is now active, queue is empty
                assert len(app._messages) == 1
                assert app._messages[0] == "second"
                assert qb.size.height == 0

                # Finish second
                app.finish_active()
                await pilot.pause()
                assert app.busy is False

        _run(_test)

    def test_not_busy_after_all_done(self):
        """busy is False when _messages is empty."""

        async def _test():
            App = _make_test_app_class()
            app = App()
            async with app.run_test(size=(80, 24)) as pilot:
                assert app.busy is False

                pi = app.query_one("#prompt-input", PromptInput)
                pi.focus()
                for ch in "hi":
                    await pilot.press(ch)
                await pilot.press("enter")
                await pilot.pause()
                assert app.busy is True

                app.finish_active()
                await pilot.pause()
                assert app.busy is False

        _run(_test)


# ── TicketPanel tests ────────────────────────────────────────────────────────


class TestTicketPanel:
    def test_empty_dir(self):
        """TicketPanel shows placeholder when no tickets dir exists."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.widgets import Collapsible, Static

            with tempfile.TemporaryDirectory() as tmpdir:

                class _App(App):
                    def compose(self) -> ComposeResult:
                        yield TicketPanel(workspace=tmpdir, id="tp")

                app = _App()
                async with app.run_test() as pilot:
                    tp = app.query_one("#tp", TicketPanel)
                    await pilot.pause()
                    # No collapsibles — just a placeholder Static
                    assert len(tp.query(Collapsible)) == 0
                    assert len(list(tp.children)) == 1

        _run(_test)

    def test_loads_ticket_json(self):
        """TicketPanel creates a Collapsible for each ticket."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.widgets import Collapsible

            with tempfile.TemporaryDirectory() as tmpdir:
                ticket_dir = Path(tmpdir) / "_agent" / "tickets"
                ticket_dir.mkdir(parents=True)
                ticket = {
                    "id": "fix-bug",
                    "title": "Fix the bug",
                    "status": "todo",
                }
                (ticket_dir / "fix-bug.json").write_text(
                    json.dumps(ticket), encoding="utf-8",
                )

                class _App(App):
                    def compose(self) -> ComposeResult:
                        yield TicketPanel(workspace=tmpdir, id="tp")

                app = _App()
                async with app.run_test() as pilot:
                    tp = app.query_one("#tp", TicketPanel)
                    await pilot.pause()
                    entries = tp.query(Collapsible)
                    assert len(entries) == 1
                    assert "fix-bug" in str(entries.first().title)

        _run(_test)

    def test_status_color_coding(self):
        """Ticket Collapsibles get CSS classes for status coloring."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.widgets import Collapsible

            with tempfile.TemporaryDirectory() as tmpdir:
                ticket_dir = Path(tmpdir) / "_agent" / "tickets"
                ticket_dir.mkdir(parents=True)
                cases = [
                    ("a-todo", "todo", "ticket-todo"),
                    ("b-wip", "in_progress", "ticket-in-progress"),
                    ("c-verify", "verify", "ticket-verify"),
                    ("d-done", "closed", "ticket-closed"),
                ]
                for tid, status, _ in cases:
                    (ticket_dir / f"{tid}.json").write_text(
                        json.dumps({"id": tid, "status": status}),
                        encoding="utf-8",
                    )

                class _App(App):
                    def compose(self) -> ComposeResult:
                        yield TicketPanel(workspace=tmpdir, id="tp")

                app = _App()
                async with app.run_test() as pilot:
                    tp = app.query_one("#tp", TicketPanel)
                    await pilot.pause()
                    entries = list(tp.query(Collapsible))
                    assert len(entries) == 4
                    for _, _, css_class in cases:
                        assert any(
                            e.has_class(css_class) for e in entries
                        )

        _run(_test)

    def test_refresh_picks_up_new_tickets(self):
        """refresh_tickets() picks up newly created ticket files."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.widgets import Collapsible

            with tempfile.TemporaryDirectory() as tmpdir:
                ticket_dir = Path(tmpdir) / "_agent" / "tickets"
                ticket_dir.mkdir(parents=True)

                class _App(App):
                    def compose(self) -> ComposeResult:
                        yield TicketPanel(workspace=tmpdir, id="tp")

                app = _App()
                async with app.run_test() as pilot:
                    tp = app.query_one("#tp", TicketPanel)
                    await pilot.pause()
                    assert len(tp.query(Collapsible)) == 0

                    (ticket_dir / "new.json").write_text(
                        json.dumps({"id": "new", "status": "todo"}),
                        encoding="utf-8",
                    )
                    tp.refresh_tickets()
                    await pilot.pause()
                    assert len(tp.query(Collapsible)) == 1

        _run(_test)

    def test_body_contains_ticket_fields(self):
        """Ticket body renders as Markdown via Static wrapping a Markdown renderable."""

        async def _test():
            from rich.markdown import Markdown as RichMarkdown
            from textual.app import App, ComposeResult
            from textual.widgets import Collapsible, Static

            with tempfile.TemporaryDirectory() as tmpdir:
                ticket_dir = Path(tmpdir) / "_agent" / "tickets"
                ticket_dir.mkdir(parents=True)
                ticket = {
                    "id": "test",
                    "title": "A title",
                    "description": "A " + "very " * 20 + "long description",
                    "status": "todo",
                }
                (ticket_dir / "test.json").write_text(
                    json.dumps(ticket), encoding="utf-8",
                )

                class _App(App):
                    def compose(self) -> ComposeResult:
                        yield TicketPanel(workspace=tmpdir, id="tp")

                app = _App()
                async with app.run_test() as pilot:
                    tp = app.query_one("#tp", TicketPanel)
                    await pilot.pause()
                    entries = tp.query(Collapsible)
                    assert len(entries) == 1
                    assert "test" in str(entries.first().title)
                    # Body Static wraps a Markdown renderable
                    from textual.widgets._collapsible import CollapsibleTitle
                    body_statics = [
                        s for s in entries.first().query(Static)
                        if not isinstance(s, CollapsibleTitle)
                    ]
                    assert len(body_statics) == 1
                    rendered = str(body_statics[0].render())
                    assert "Markdown" in rendered

        _run(_test)


# ── AvailableToolsPanel tests ────────────────────────────────────────────────


class TestAvailableToolsPanel:
    def test_groups_tools_by_category(self):
        """Panel groups tools and shows collapsible sections per group."""

        async def _test():
            from textual.app import App, ComposeResult

            tools = [
                {"name": "Read", "description": "", "group": "Built-in"},
                {"name": "Edit", "description": "", "group": "Built-in"},
                {"name": "lint", "description": "Run linter", "group": "MCP"},
            ]

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield AvailableToolsPanel(tools=tools, id="atp")

            app = _App()
            async with app.run_test():
                panel = app.query_one("#atp", AvailableToolsPanel)
                # Direct children are group-level collapsibles
                group_sections = [
                    c for c in panel.children if isinstance(c, Collapsible)
                ]
                assert len(group_sections) == 2
                titles = {str(s.title) for s in group_sections}
                assert "Built-in (2)" in titles
                assert "MCP (1)" in titles

        _run(_test)

    def test_empty_tools_shows_placeholder(self):
        """Panel with no tools shows a placeholder message."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.widgets import Static

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield AvailableToolsPanel(tools=[], id="atp")

            app = _App()
            async with app.run_test():
                panel = app.query_one("#atp", AvailableToolsPanel)
                statics = panel.query(Static)
                texts = [s.render().plain for s in statics]
                assert any("(no tools registered)" in t for t in texts)

        _run(_test)

    def test_tab_exists_in_compose(self):
        """AgentApp compose includes the Available tab."""

        async def _test():
            opts = _make_mock_options()
            tools_meta = [
                {"name": "Bash", "description": "", "group": "Built-in"},
            ]
            with patch("repo_tools.agent.tui.AgentApp._client_loop"):
                app = AgentApp(options=opts, tools_metadata=tools_meta)
                async with app.run_test():
                    pane = app.query_one("#tab-available")
                    assert pane is not None

        _run(_test)


# ── ToolLog tests ────────────────────────────────────────────────────────────


class TestToolLog:
    def test_add_tool_creates_collapsed_entry(self):
        """add_tool mounts a collapsed collapsible with pending icon and class."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolLog(id="tl")

            app = _App()
            async with app.run_test() as pilot:
                tl = app.query_one("#tl", ToolLog)
                tl.add_tool("t1", "Bash", {"command": "ls -la"})
                await pilot.pause()
                assert "t1" in tl._entries
                entry, summary = tl._entries["t1"]
                assert summary == "Bash(ls -la)"
                assert entry.collapsed
                assert "\u23f3" in str(entry.title)
                assert entry.has_class("tool-pending")

        _run(_test)

    def test_set_result_updates_title_icon(self):
        """set_result changes icon to \u2713 and class to tool-success."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolLog(id="tl")

            app = _App()
            async with app.run_test() as pilot:
                tl = app.query_one("#tl", ToolLog)
                tl.add_tool("t1", "Read", {"file_path": "/foo"})
                await pilot.pause()
                entry, _ = tl._entries["t1"]
                assert entry.collapsed
                assert entry.has_class("tool-pending")

                tl.set_result("t1", "file contents here")
                await pilot.pause()
                title = str(entry.title)
                assert "\u2713" in title
                assert "Read(/foo)" in title
                assert not entry.has_class("tool-pending")
                assert entry.has_class("tool-success")

        _run(_test)

    def test_error_result_shows_cross(self):
        """Error results show \u2717 icon and tool-error class."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolLog(id="tl")

            app = _App()
            async with app.run_test() as pilot:
                tl = app.query_one("#tl", ToolLog)
                tl.add_tool("t1", "Bash", {"command": "fail"})
                tl.set_result("t1", "command failed", is_error=True)
                await pilot.pause()
                entry, _ = tl._entries["t1"]
                title = str(entry.title)
                assert "\u2717" in title
                assert "Bash(fail)" in title
                assert not entry.has_class("tool-pending")
                assert entry.has_class("tool-error")

        _run(_test)

    def test_set_result_shows_output(self):
        """set_result with output reveals the pre-created output widget."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolLog(id="tl")

            app = _App()
            async with app.run_test() as pilot:
                tl = app.query_one("#tl", ToolLog)
                tl.add_tool("t1", "Bash", {"command": "echo hi"})
                await pilot.pause()
                out = tl._output_widgets["t1"]
                assert not out.display

                tl.set_result("t1", "hello world")
                await pilot.pause()
                assert out.display
                assert "hello world" in str(out.render())

        _run(_test)

    def test_set_result_error_adds_error_class(self):
        """Error output gets tool-log-error class on the output widget."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolLog(id="tl")

            app = _App()
            async with app.run_test() as pilot:
                tl = app.query_one("#tl", ToolLog)
                tl.add_tool("t1", "Bash", {"command": "fail"})
                await pilot.pause()
                tl.set_result("t1", "command failed", is_error=True)
                await pilot.pause()
                out = tl._output_widgets["t1"]
                assert out.display
                assert out.has_class("tool-log-error")

        _run(_test)

    def test_set_result_empty_output_stays_hidden(self):
        """set_result with empty output leaves the output widget hidden."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolLog(id="tl")

            app = _App()
            async with app.run_test() as pilot:
                tl = app.query_one("#tl", ToolLog)
                tl.add_tool("t1", "Read", None)
                await pilot.pause()
                tl.set_result("t1", "")
                await pilot.pause()
                out = tl._output_widgets["t1"]
                assert not out.display

        _run(_test)

    def test_nested_tool_under_parent(self):
        """add_tool with parent_id mounts child inside parent Collapsible."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolLog(id="tl")

            app = _App()
            async with app.run_test() as pilot:
                tl = app.query_one("#tl", ToolLog)
                tl.add_tool("parent-1", "Task", {"description": "explore"})
                await pilot.pause()
                tl.add_tool("child-1", "Read", {"file_path": "/a.py"}, parent_id="parent-1")
                await pilot.pause()

                parent_entry, _ = tl._entries["parent-1"]
                child_entry, _ = tl._entries["child-1"]

                # Child should be mounted inside the parent Collapsible
                nested = parent_entry.query(Collapsible)
                assert child_entry in nested

        _run(_test)

    def test_nested_tool_unknown_parent_mounts_top_level(self):
        """add_tool with unknown parent_id mounts at top level."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolLog(id="tl")

            app = _App()
            async with app.run_test() as pilot:
                tl = app.query_one("#tl", ToolLog)
                tl.add_tool("child-1", "Read", None, parent_id="nonexistent")
                await pilot.pause()
                child_entry, _ = tl._entries["child-1"]
                # Should be a direct child of ToolLog
                assert child_entry.parent is tl

        _run(_test)

    def test_multiple_tools_tracked(self):
        """Multiple tool calls are tracked independently."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolLog(id="tl")

            app = _App()
            async with app.run_test() as pilot:
                tl = app.query_one("#tl", ToolLog)
                tl.add_tool("t1", "Read", None)
                tl.add_tool("t2", "Edit", None)
                await pilot.pause()
                assert len(tl._entries) == 2

                tl.set_result("t1", "ok")
                await pilot.pause()
                # Both stay collapsed (collapsed by default)
                assert tl._entries["t1"][0].collapsed
                assert tl._entries["t2"][0].collapsed

        _run(_test)

    def test_clear_removes_entries_and_children(self):
        """clear() empties _entries, _output_widgets, and removes all child widgets."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolLog(id="tl")

            app = _App()
            async with app.run_test() as pilot:
                tl = app.query_one("#tl", ToolLog)
                tl.add_tool("t1", "Read", None)
                tl.add_tool("t2", "Edit", None)
                await pilot.pause()
                assert len(tl._entries) == 2
                assert len(tl._output_widgets) == 2
                assert len(tl.query(Collapsible)) == 2

                tl.clear()
                await pilot.pause()
                assert len(tl._entries) == 0
                assert len(tl._output_widgets) == 0
                assert len(tl.query(Collapsible)) == 0

        _run(_test)

    def test_markup_like_output_does_not_crash(self):
        """Tool output containing Rich-markup-like characters renders without error."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolLog(id="tl")

            app = _App()
            async with app.run_test() as pilot:
                tl = app.query_one("#tl", ToolLog)
                tl.add_tool("t1", "Bash", {"command": "echo json"})
                await pilot.pause()

                # This string looks like Rich markup and would crash without markup=False
                markup_output = '[{"name": "foo", "value=\'bar\'"}]'
                tl.set_result("t1", markup_output)
                await pilot.pause()

                out = tl._output_widgets["t1"]
                assert out.display
                assert markup_output in str(out.render())

        _run(_test)


# ── _summarize_tool tests ──────────────────────────────────────────────────


class TestSummarizeTool:
    def test_bash_command(self):
        assert _summarize_tool("Bash", {"command": "ls -la"}) == "Bash(ls -la)"

    def test_read_file_path(self):
        assert _summarize_tool("Read", {"file_path": "/foo/bar.py"}) == "Read(/foo/bar.py)"

    def test_edit_file_path(self):
        assert _summarize_tool("Edit", {"file_path": "/a.py"}) == "Edit(/a.py)"

    def test_glob_pattern(self):
        assert _summarize_tool("Glob", {"pattern": "**/*.py"}) == "Glob(**/*.py)"

    def test_grep_pattern(self):
        assert _summarize_tool("Grep", {"pattern": "TODO"}) == "Grep(TODO)"

    def test_no_args(self):
        assert _summarize_tool("Bash", None) == "Bash()"
        assert _summarize_tool("Bash", {}) == "Bash()"

    def test_truncation(self):
        long_cmd = "x" * 100
        result = _summarize_tool("Bash", {"command": long_cmd})
        assert len(result) <= 70  # "Bash(" + 60 + ")"
        assert result.endswith("...)")

    def test_unknown_tool_first_string(self):
        result = _summarize_tool("CustomTool", {"url": "http://example.com"})
        assert result == "CustomTool(http://example.com)"

    def test_unknown_tool_no_string(self):
        result = _summarize_tool("CustomTool", {"count": 42})
        assert result == "CustomTool(...)"


# ── _format_tool_body tests ─────────────────────────────────────────────────


class TestFormatToolBody:
    def test_no_args(self):
        assert _format_tool_body("Bash", None) == "(no args)"
        assert _format_tool_body("Bash", {}) == "(no args)"

    def test_json_syntax_for_generic_tool(self):
        result = _format_tool_body("Read", {"file_path": "/foo.py"})
        assert isinstance(result, Syntax)
        assert result._lexer == "json"

    def test_edit_rendered_as_json(self):
        args = {
            "file_path": "a.py",
            "old_string": "hello",
            "new_string": "world",
        }
        result = _format_tool_body("Edit", args)
        assert isinstance(result, Syntax)
        assert result._lexer == "json"


# ── StatusBar busy indicator tests ───────────────────────────────────────────


class TestStatusBarBusy:
    def test_starts_ready_green(self):
        """StatusBar starts with 'Ready' and green class."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield StatusBar(id="sb")

            app = _App()
            async with app.run_test() as pilot:
                sb = app.query_one("#sb", StatusBar)
                await pilot.pause()
                assert "Ready" in str(sb.render())
                assert sb.has_class("status-ready")

        _run(_test)

    def test_set_status_working(self):
        """set_status('working') switches to working class."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield StatusBar(id="sb")

            app = _App()
            async with app.run_test() as pilot:
                sb = app.query_one("#sb", StatusBar)
                sb.set_status("Working...", "working")
                await pilot.pause()
                assert "Working" in str(sb.render())
                assert sb.has_class("status-working")
                assert not sb.has_class("status-ready")

        _run(_test)

    def test_set_status_error(self):
        """set_status('error') switches to error class."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield StatusBar(id="sb")

            app = _App()
            async with app.run_test() as pilot:
                sb = app.query_one("#sb", StatusBar)
                sb.set_status("Error", "error")
                await pilot.pause()
                assert sb.has_class("status-error")

        _run(_test)

    def test_set_status_back_to_ready(self):
        """Transitioning back to ready removes working class."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield StatusBar(id="sb")

            app = _App()
            async with app.run_test() as pilot:
                sb = app.query_one("#sb", StatusBar)
                sb.set_status("Working...", "working")
                sb.set_status("Done", "ready")
                await pilot.pause()
                assert sb.has_class("status-ready")
                assert not sb.has_class("status-working")

        _run(_test)


# ── F2 side pane toggle tests ───────────────────────────────────────────────


class TestSidePaneToggle:
    def test_f2_hides_side_pane(self):
        """F2 toggles side pane visibility in a layout with TabbedContent."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.binding import Binding as _Binding
            from textual.containers import Horizontal, VerticalScroll
            from textual.widgets import TabbedContent, TabPane, Static

            class _App(App):
                CSS = """
                #main-area { height: 1fr; }
                #chat-log { width: 7fr; }
                #side-pane { width: 3fr; }
                """
                BINDINGS = [
                    _Binding("f2", "toggle_side", show=False),
                ]
                _visible = True

                def compose(self) -> ComposeResult:
                    with Horizontal(id="main-area"):
                        yield VerticalScroll(id="chat-log")
                        with TabbedContent(id="side-pane"):
                            yield TabPane("Info", Static("hi"), id="t1")

                def action_toggle_side(self):
                    pane = self.query_one("#side-pane")
                    self._visible = not self._visible
                    pane.styles.display = (
                        "block" if self._visible else "none"
                    )

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                sp = app.query_one("#side-pane")
                assert sp.styles.display != "none"

                await pilot.press("f2")
                await pilot.pause()
                assert sp.styles.display == "none"

                await pilot.press("f2")
                await pilot.pause()
                assert sp.styles.display == "block"

        _run(_test)


# ── F3 wrap/scroll toggle tests ─────────────────────────────────────────────


class TestWrapScrollToggle:
    def test_f3_toggles_hscroll_class_on_panels(self):
        """F3 adds .hscroll class to VerticalScroll panels and removes it on second press."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.binding import Binding as _Binding
            from textual.containers import Horizontal, Vertical, VerticalScroll
            from textual.widgets import (
                RichLog, Static, TabbedContent, TabPane,
            )

            class _App(App):
                CSS = """
                #main-area { height: 1fr; }
                #chat-log { width: 7fr; }
                #side-container { width: 3fr; }
                #side-pane { width: 1fr; }
                #wrap-toggle { height: 1; dock: bottom; }
                .hscroll { overflow-x: auto !important; }
                """
                BINDINGS = [
                    _Binding("f3", "toggle_wrap", show=False),
                ]
                _side_wrap = True

                def compose(self) -> ComposeResult:
                    with Horizontal(id="main-area"):
                        yield VerticalScroll(id="chat-log")
                        with Vertical(id="side-container"):
                            with TabbedContent(id="side-pane"):
                                yield TabPane(
                                    "Tools",
                                    VerticalScroll(id="tool-log"),
                                    id="t1",
                                )
                                yield TabPane(
                                    "Logs",
                                    RichLog(
                                        id="log-pane", wrap=True,
                                        highlight=True,
                                    ),
                                    id="t2",
                                )
                            yield Static("[F3 Wrap]", id="wrap-toggle")

                def action_toggle_wrap(self):
                    self._side_wrap = not self._side_wrap
                    try:
                        panel = self.query_one(
                            "#tool-log", VerticalScroll,
                        )
                        if self._side_wrap:
                            panel.remove_class("hscroll")
                        else:
                            panel.add_class("hscroll")
                    except Exception:
                        pass
                    try:
                        log = self.query_one("#log-pane", RichLog)
                        log.wrap = self._side_wrap
                    except Exception:
                        pass
                    try:
                        toggle = self.query_one("#wrap-toggle", Static)
                        toggle.update(
                            "[F3 Wrap]" if self._side_wrap
                            else "[F3 Scroll]",
                        )
                    except Exception:
                        pass

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                panel = app.query_one("#tool-log", VerticalScroll)
                log_pane = app.query_one("#log-pane", RichLog)
                toggle = app.query_one("#wrap-toggle", Static)

                # Initially in wrap mode
                assert "hscroll" not in panel.classes
                assert log_pane.wrap is True

                # F3 switches to scroll mode
                await pilot.press("f3")
                await pilot.pause()
                assert "hscroll" in panel.classes
                assert log_pane.wrap is False
                assert "[F3 Scroll]" in toggle.content

                # F3 again switches back to wrap mode
                await pilot.press("f3")
                await pilot.pause()
                assert "hscroll" not in panel.classes
                assert log_pane.wrap is True
                assert "[F3 Wrap]" in toggle.content

        _run(_test)

    def test_f2_hides_side_container(self):
        """F2 hides #side-container (including wrap-toggle toolbar)."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.binding import Binding as _Binding
            from textual.containers import Horizontal, Vertical, VerticalScroll
            from textual.widgets import Static, TabbedContent, TabPane

            class _App(App):
                CSS = """
                #main-area { height: 1fr; }
                #chat-log { width: 7fr; }
                #side-container { width: 3fr; }
                """
                BINDINGS = [
                    _Binding("f2", "toggle_side", show=False),
                ]
                _visible = True

                def compose(self) -> ComposeResult:
                    with Horizontal(id="main-area"):
                        yield VerticalScroll(id="chat-log")
                        with Vertical(id="side-container"):
                            with TabbedContent(id="side-pane"):
                                yield TabPane(
                                    "Info", Static("hi"), id="t1",
                                )
                            yield Static("[F3 Wrap]", id="wrap-toggle")

                def action_toggle_side(self):
                    self._visible = not self._visible
                    container = self.query_one(
                        "#side-container", Vertical,
                    )
                    container.display = self._visible

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                sc = app.query_one("#side-container", Vertical)
                assert sc.display is True

                await pilot.press("f2")
                await pilot.pause()
                assert sc.display is False

                await pilot.press("f2")
                await pilot.pause()
                assert sc.display is True

        _run(_test)


# ── Slash command tests ─────────────────────────────────────────────────────


class TestSlashCommands:
    def test_exit_command(self):
        """/exit and /quit are intercepted; unknown /commands are rejected."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.containers import VerticalScroll
            from textual.widgets import Static

            sent = []

            class _App(App):
                CSS = "#chat-log { height: 1fr; }"

                def compose(self) -> ComposeResult:
                    yield VerticalScroll(id="chat-log")
                    yield StatusBar(id="status-bar")
                    yield QueueBar(id="queue-bar")
                    yield PromptInput(id="prompt-input")

                async def on_prompt_input_submitted(self, event):
                    cmd = event.text.strip()
                    if cmd in ("/exit", "/quit"):
                        return
                    if cmd.startswith("/"):
                        chat_log = self.query_one("#chat-log", VerticalScroll)
                        await chat_log.mount(Static(f"Unknown command: {cmd}"))
                        return
                    sent.append(event.text)

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                pi = app.query_one("#prompt-input", PromptInput)
                pi.focus()

                # /exit is intercepted
                for ch in "/exit":
                    await pilot.press(ch)
                await pilot.press("enter")
                await pilot.pause()
                assert sent == []

                # /exot is rejected as unknown command
                for ch in "/exot":
                    await pilot.press(ch)
                await pilot.press("enter")
                await pilot.pause()
                assert sent == []
                chat_log = app.query_one("#chat-log", VerticalScroll)
                statics = chat_log.query(Static)
                assert any("Unknown command" in str(s.render()) for s in statics)

        _run(_test)

    def test_clear_command_resets_ui(self):
        """/clear clears chat log, tool log, and resets status bar."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.containers import VerticalScroll
            from textual.widgets import Static

            client_loop_calls = []

            class _App(App):
                CSS = "#chat-log { height: 1fr; }"

                def compose(self) -> ComposeResult:
                    yield VerticalScroll(id="chat-log")
                    yield ToolLog(id="tool-log")
                    yield StatusBar(id="status-bar")
                    yield QueueBar(id="queue-bar")
                    yield PromptInput(id="prompt-input")

                _query_active = False
                _pending_tools: dict = {}
                _current_tool_group = None
                _queued_input: list = []
                _choice_future = None

                def _client_loop(self):
                    client_loop_calls.append(1)

                def _refresh_queue_bar(self):
                    self.query_one("#queue-bar", QueueBar).refresh_queue(
                        self._queued_input,
                    )

                async def on_prompt_input_submitted(self, event):
                    cmd = event.text.strip()
                    if cmd == "/clear":
                        self._queued_input.clear()
                        self._refresh_queue_bar()
                        self._pending_tools.clear()
                        self._current_tool_group = None
                        self.query_one(
                            "#chat-log", VerticalScroll,
                        ).remove_children()
                        self.query_one("#tool-log", ToolLog).clear()
                        self.query_one(
                            "#status-bar", StatusBar,
                        ).set_status("Ready", "ready")
                        self._client_loop()
                        return

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                # Populate some state
                chat_log = app.query_one("#chat-log", VerticalScroll)
                await chat_log.mount(Static("hello"))
                tl = app.query_one("#tool-log", ToolLog)
                tl.add_tool("t1", "Bash", {"command": "ls"})
                app.query_one("#status-bar", StatusBar).set_status(
                    "Working...", "working",
                )
                await pilot.pause()

                assert len(chat_log.query(Static)) > 0
                assert len(tl._entries) > 0

                # Type /clear and submit
                pi = app.query_one("#prompt-input", PromptInput)
                pi.focus()
                for ch in "/clear":
                    await pilot.press(ch)
                await pilot.press("enter")
                await pilot.pause()

                # Chat log cleared
                assert len(chat_log.query(Static)) == 0
                # Tool log cleared
                assert len(tl._entries) == 0
                # Status bar back to ready
                sb = app.query_one("#status-bar", StatusBar)
                assert sb.has_class("status-ready")
                # Client loop restarted
                assert len(client_loop_calls) == 1

        _run(_test)


# ── _ticket_to_markdown tests ──────────────────────────────────────────────


class TestTicketToMarkdown:
    def test_basic_fields(self):
        data = {"id": "t1", "title": "Fix bug", "status": "todo"}
        md = _ticket_to_markdown(data)
        assert "**title:** Fix bug" in md
        assert "**status:** todo" in md
        assert "id" not in md.split("**")[0]  # id key skipped

    def test_list_values(self):
        data = {"criteria": ["passes tests", "no regressions"]}
        md = _ticket_to_markdown(data)
        assert "**criteria:**" in md
        assert "- passes tests" in md
        assert "- no regressions" in md

    def test_dict_values(self):
        data = {"meta": {"author": "alice", "priority": "high"}}
        md = _ticket_to_markdown(data)
        assert "**meta:**" in md
        assert "- author: alice" in md

    def test_empty_list(self):
        md = _ticket_to_markdown({"tags": []})
        assert "**tags:** (none)" in md

    def test_empty_dict(self):
        md = _ticket_to_markdown({"meta": {}})
        assert "**meta:** (none)" in md

    def test_empty_data(self):
        assert _ticket_to_markdown({}) == "(empty)"


# ── Log pane tests ────────────────────────────────────────────────────────


class TestLogPane:
    def test_richlog_mounted_in_logs_tab(self):
        """Logs tab contains a RichLog widget."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.widgets import RichLog, TabbedContent, TabPane

            class _App(App):
                def compose(self) -> ComposeResult:
                    with TabbedContent(id="side-pane"):
                        yield TabPane(
                            "Logs",
                            RichLog(id="log-pane", wrap=True, highlight=True),
                            id="tab-logs",
                        )

            app = _App()
            async with app.run_test() as pilot:
                await pilot.pause()
                rl = app.query_one("#log-pane", RichLog)
                assert rl is not None

        _run(_test)

    def test_tui_log_handler_writes(self):
        """TUILogHandler.emit() writes formatted messages to RichLog."""

        async def _test():
            import logging as _logging

            from textual.app import App, ComposeResult
            from textual.widgets import RichLog

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield RichLog(id="log-pane", wrap=True)

            app = _App()
            async with app.run_test() as pilot:
                rl = app.query_one("#log-pane", RichLog)
                handler = TUILogHandler(rl)
                handler.setFormatter(_logging.Formatter("%(message)s"))
                record = _logging.LogRecord(
                    name="test", level=_logging.INFO, pathname="",
                    lineno=0, msg="hello from handler", args=(), exc_info=None,
                )
                handler.emit(record)
                await pilot.pause()
                assert len(rl.lines) > 0

        _run(_test)


# ── _summarize_tool additions ──────────────────────────────────────────────


class TestSummarizeToolAdditions:
    def test_webfetch_url(self):
        assert _summarize_tool("WebFetch", {"url": "https://example.com"}) == "WebFetch(https://example.com)"

    def test_websearch_query(self):
        assert _summarize_tool("WebSearch", {"query": "python docs"}) == "WebSearch(python docs)"

    def test_task_description(self):
        assert _summarize_tool("Task", {"description": "find files"}) == "Task(find files)"

    def test_agent_description(self):
        assert _summarize_tool("Agent", {"description": "explore codebase"}) == "Agent(explore codebase)"

    def test_todowrite_shows_progress(self):
        todos = [
            {"content": "Run tests", "status": "completed", "activeForm": "Running tests"},
            {"content": "Build", "status": "in_progress", "activeForm": "Building"},
            {"content": "Deploy", "status": "pending", "activeForm": "Deploying"},
        ]
        result = _summarize_tool("TodoWrite", {"todos": todos})
        assert "1/3" in result
        assert "Building" in result

    def test_todowrite_empty_todos(self):
        assert _summarize_tool("TodoWrite", {"todos": []}) == "TodoWrite()"

    def test_todowrite_no_args(self):
        assert _summarize_tool("TodoWrite", None) == "TodoWrite()"


# ── ChoicePanel tests ──────────────────────────────────────────────────────


class TestChoicePanel:
    def test_renders_question_and_options(self):
        """ChoicePanel displays question text and numbered options."""

        async def _test():
            from textual.app import App, ComposeResult

            questions = [{
                "question": "Which approach?",
                "options": [
                    {"label": "Fast", "description": "Quick but rough"},
                    {"label": "Thorough", "description": "Slow but careful"},
                ],
            }]

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ChoicePanel(questions, id="cp")

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                cp = app.query_one("#cp", ChoicePanel)
                await pilot.pause()
                rendered = str(cp.render())
                assert "Which approach?" in rendered
                assert "1." in rendered
                assert "Fast" in rendered
                assert "2." in rendered
                assert "Thorough" in rendered

        _run(_test)

    def test_multiple_questions(self):
        """ChoicePanel handles multiple questions."""

        async def _test():
            from textual.app import App, ComposeResult

            questions = [
                {"question": "Q1?", "options": [{"label": "A"}, {"label": "B"}]},
                {"question": "Q2?", "options": [{"label": "X"}, {"label": "Y"}]},
            ]

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ChoicePanel(questions, id="cp")

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                cp = app.query_one("#cp", ChoicePanel)
                await pilot.pause()
                rendered = str(cp.render())
                assert "Q1?" in rendered
                assert "Q2?" in rendered

        _run(_test)


# ── _parse_choice_answers tests ────────────────────────────────────────────


class TestParseChoiceAnswers:
    _QUESTIONS = [
        {
            "question": "Which approach?",
            "options": [
                {"label": "Fast"},
                {"label": "Thorough"},
            ],
        },
    ]

    def test_number_maps_to_label(self):
        result = _parse_choice_answers("1", self._QUESTIONS)
        assert result == {"Which approach?": "Fast"}

    def test_second_option(self):
        result = _parse_choice_answers("2", self._QUESTIONS)
        assert result == {"Which approach?": "Thorough"}

    def test_free_text(self):
        result = _parse_choice_answers("something custom", self._QUESTIONS)
        assert result == {"Which approach?": "something custom"}

    def test_out_of_range_number(self):
        result = _parse_choice_answers("99", self._QUESTIONS)
        assert result == {"Which approach?": "99"}

    def test_multiple_questions_semicolon(self):
        questions = [
            {"question": "Q1?", "options": [{"label": "A"}, {"label": "B"}]},
            {"question": "Q2?", "options": [{"label": "X"}, {"label": "Y"}]},
        ]
        result = _parse_choice_answers("1; 2", questions)
        assert result == {"Q1?": "A", "Q2?": "Y"}

    def test_missing_answer_defaults_empty(self):
        questions = [
            {"question": "Q1?", "options": [{"label": "A"}]},
            {"question": "Q2?", "options": [{"label": "X"}]},
        ]
        result = _parse_choice_answers("1", questions)
        assert result == {"Q1?": "A", "Q2?": ""}


# ── AskUserQuestion in ALLOWED_TOOLS test ──────────────────────────────────


class TestAllowedTools:
    def test_ask_user_question_in_allowed_tools(self):
        from repo_tools.agent.claude._shared import ALLOWED_TOOLS
        assert "AskUserQuestion" in ALLOWED_TOOLS


# ── Handle ToolResult in UserMessage tests ─────────────────────────────────


class TestHandleToolResultInUserMessage:
    def test_tool_result_in_user_message_clears_pending(self):
        """ToolResultBlock inside SDK UserMessage resolves pending tool icons."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.containers import VerticalScroll

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield VerticalScroll(id="chat-log")
                    yield ToolLog(id="tool-log")

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                # Simulate: a tool was registered as pending
                tg = ToolCallGroup()
                chat_log = app.query_one("#chat-log", VerticalScroll)
                await chat_log.mount(tg)
                tg.add_tool("tool-123", "Read")
                await pilot.pause()

                # Before result: should show ▸
                rendered = str(tg.render())
                assert "\u25b8" in rendered

                # Simulate tool result
                tg.set_result("tool-123", is_error=False)
                await pilot.pause()

                # After result: should show ✓
                rendered = str(tg.render())
                assert "\u2713" in rendered
                assert "\u25b8" not in rendered

        _run(_test)

    def test_result_message_flushes_remaining_pending(self):
        """Pending tools left over at ResultMessage time are flushed to ✓."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.containers import VerticalScroll

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield VerticalScroll(id="chat-log")
                    yield ToolLog(id="tool-log")

            app = _App()
            async with app.run_test(size=(80, 24)) as pilot:
                chat_log = app.query_one("#chat-log", VerticalScroll)
                tg = ToolCallGroup()
                await chat_log.mount(tg)

                # Register two tools, only resolve one
                tg.add_tool("t1", "Read")
                tg.add_tool("t2", "Edit")
                tg.set_result("t1", is_error=False)
                await pilot.pause()

                # t2 still pending
                rendered = str(tg.render())
                assert "\u25b8" in rendered  # t2 still has ▸

                # Simulate the flush that ResultMessage does
                tg.set_result("t2", is_error=False)
                await pilot.pause()

                rendered = str(tg.render())
                assert "\u25b8" not in rendered
                assert rendered.count("\u2713") == 2

        _run(_test)


# ── ExitPlanMode approval tests ──────────────────────────────────────────────


def _noop_client_app_class():
    """Build an AgentApp subclass with _client_loop disabled for tests."""
    from textual import work

    class _NoClientApp(AgentApp):
        @work(group="client")
        async def _client_loop(self) -> None:
            pass

    return _NoClientApp


class TestExitPlanModeApproval:
    """ExitPlanMode: interactive Accept bar + reject-via-prompt."""

    def test_accept_via_bar_returns_allow(self):
        """PlanApprovalBar.Accepted resolves the future as allow."""

        async def _test():
            App = _noop_client_app_class()
            app = App(options=_make_mock_options())
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()
                result_holder: list = []

                async def _call():
                    r = await app._exit_plan_mode_hook(
                        {"allowedPrompts": []}, None, None,
                    )
                    result_holder.append(r)

                task = loop.create_task(_call())
                await pilot.pause()
                await pilot.pause()

                # Simulate the Accept bar being activated
                app._choice_future.set_result(_PLAN_ACCEPTED)
                await task

                assert result_holder
                assert result_holder[0] == {}

                # Bar should be deactivated after approval
                bar = app.query_one("#plan-approval", PlanApprovalBar)
                assert not bar.has_class("plan-active")

        _run(_test)

    def test_feedback_returns_deny(self):
        """Typing feedback in the prompt rejects the plan."""

        async def _test():
            App = _noop_client_app_class()
            app = App(options=_make_mock_options())
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()
                result_holder: list = []

                async def _call():
                    r = await app._exit_plan_mode_hook(
                        {"allowedPrompts": []}, None, None,
                    )
                    result_holder.append(r)

                task = loop.create_task(_call())
                await pilot.pause()
                await pilot.pause()

                app._choice_future.set_result("needs more error handling")
                await task

                assert result_holder
                hook_out = result_holder[0]["hookSpecificOutput"]
                assert hook_out["permissionDecision"] == "deny"
                assert "error handling" in hook_out["permissionDecisionReason"]

        _run(_test)

    def test_cancel_returns_deny(self):
        """Cancelling the Future (Ctrl+C) returns Deny with 'interrupted'."""

        async def _test():
            App = _noop_client_app_class()
            app = App(options=_make_mock_options())
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()
                result_holder: list = []

                async def _call():
                    r = await app._exit_plan_mode_hook(
                        {}, None, None,
                    )
                    result_holder.append(r)

                task = loop.create_task(_call())
                await pilot.pause()
                await pilot.pause()

                app._choice_future.cancel()
                await task

                assert result_holder
                hook_out = result_holder[0]["hookSpecificOutput"]
                assert hook_out["permissionDecision"] == "deny"
                assert "interrupted" in hook_out["permissionDecisionReason"]

        _run(_test)

    def test_approval_bar_visible_during_review(self):
        """PlanApprovalBar gets plan-active class during review."""

        async def _test():
            App = _noop_client_app_class()
            app = App(options=_make_mock_options())
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()

                async def _call():
                    await app._exit_plan_mode_hook(
                        {}, None, None,
                    )

                task = loop.create_task(_call())
                await pilot.pause()
                await pilot.pause()

                bar = app.query_one("#plan-approval", PlanApprovalBar)
                assert bar.has_class("plan-active")

                app._choice_future.set_result(_PLAN_ACCEPTED)
                await task

        _run(_test)


# ── AskUserQuestion hook tests ─────────────────────────────────────────────


class TestAskUserQuestionHook:
    """_ask_user_question_hook: allow with updatedInput containing answers."""

    _QUESTIONS = [
        {
            "question": "Pick a color?",
            "header": "Color",
            "options": [
                {"label": "Red", "description": "Warm"},
                {"label": "Blue", "description": "Cool"},
            ],
            "multiSelect": False,
        },
    ]

    def test_answer_returns_allow_with_updated_input(self):
        """Selecting an option returns allow with answers in updatedInput."""

        async def _test():
            App = _noop_client_app_class()
            app = App(options=_make_mock_options())
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()
                result_holder: list = []

                tool_input = {"questions": self._QUESTIONS}

                async def _call():
                    r = await app._ask_user_question_hook(
                        {"tool_input": tool_input}, None, None,
                    )
                    result_holder.append(r)

                task = loop.create_task(_call())
                await pilot.pause()
                await pilot.pause()

                # Simulate user typing "1" (selects "Red")
                app._choice_future.set_result("1")
                await task

                assert result_holder
                hook_out = result_holder[0]["hookSpecificOutput"]
                assert hook_out["permissionDecision"] == "allow"
                assert "updatedInput" in hook_out
                answers = hook_out["updatedInput"]["answers"]
                assert answers["Pick a color?"] == "Red"

        _run(_test)

    def test_cancel_returns_deny(self):
        """Cancelling during AskUserQuestion returns deny."""

        async def _test():
            App = _noop_client_app_class()
            app = App(options=_make_mock_options())
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()
                result_holder: list = []

                tool_input = {"questions": self._QUESTIONS}

                async def _call():
                    r = await app._ask_user_question_hook(
                        {"tool_input": tool_input}, None, None,
                    )
                    result_holder.append(r)

                task = loop.create_task(_call())
                await pilot.pause()
                await pilot.pause()

                app._choice_future.cancel()
                await task

                assert result_holder
                hook_out = result_holder[0]["hookSpecificOutput"]
                assert hook_out["permissionDecision"] == "deny"
                assert "interrupted" in hook_out["permissionDecisionReason"]

        _run(_test)

    def test_empty_questions_returns_empty(self):
        """No questions → empty dict (no hook interference)."""

        async def _test():
            App = _noop_client_app_class()
            app = App(options=_make_mock_options())
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()

                r = await app._ask_user_question_hook(
                    {"tool_input": {"questions": []}}, None, None,
                )
                assert r == {}

        _run(_test)


# ── Ticket approval hook tests ─────────────────────────────────────────────


class TestApproveTicketHook:
    """_approve_ticket_hook: gate create_ticket with user approval."""

    _TOOL_INPUT = {
        "id": "add-retry",
        "title": "Add retry logic",
        "description": "Wrap HTTP calls in a retry loop.",
        "criteria": ["Retries on 5xx", "Tests pass"],
    }

    def test_approve_returns_allow(self):
        """Selecting 'Create ticket' lets the tool proceed."""

        async def _test():
            App = _noop_client_app_class()
            app = App(
                options=_make_mock_options(), human_ticket_review=True,
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()
                result_holder: list = []

                async def _call():
                    r = await app._approve_ticket_hook(
                        {"tool_input": self._TOOL_INPUT}, None, None,
                    )
                    result_holder.append(r)

                task = loop.create_task(_call())
                await pilot.pause()
                await pilot.pause()

                # "1" selects "Create ticket"
                app._choice_future.set_result("1")
                await task

                assert result_holder
                assert result_holder[0] == {}

        _run(_test)

    def test_skip_returns_deny(self):
        """Selecting 'Skip' denies the tool call."""

        async def _test():
            App = _noop_client_app_class()
            app = App(
                options=_make_mock_options(), human_ticket_review=True,
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()
                result_holder: list = []

                async def _call():
                    r = await app._approve_ticket_hook(
                        {"tool_input": self._TOOL_INPUT}, None, None,
                    )
                    result_holder.append(r)

                task = loop.create_task(_call())
                await pilot.pause()
                await pilot.pause()

                # "2" selects "Skip"
                app._choice_future.set_result("2")
                await task

                assert result_holder
                hook_out = result_holder[0]["hookSpecificOutput"]
                assert hook_out["permissionDecision"] == "deny"
                assert "add-retry" in hook_out["permissionDecisionReason"]

        _run(_test)

    def test_required_criteria_merged_in_display(self):
        """Required criteria from project config appear in the review panel."""

        async def _test():
            App = _noop_client_app_class()
            required = ["Lint passes", "Tests pass"]
            app = App(
                options=_make_mock_options(), human_ticket_review=True,
                required_criteria=required,
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()

                async def _call():
                    return await app._approve_ticket_hook(
                        {"tool_input": self._TOOL_INPUT}, None, None,
                    )

                task = loop.create_task(_call())
                await pilot.pause()
                await pilot.pause()

                # Inspect the question text shown to the user — it should
                # contain both user-provided AND required criteria.
                assert app._choice_questions is not None
                q_text = app._choice_questions[0]["question"]
                # "Tests pass" is in both user and required — should appear once
                assert "Retries on 5xx" in q_text
                assert "Lint passes" in q_text
                assert "Tests pass" in q_text

                app._choice_future.set_result("1")
                await task

        _run(_test)

    def test_cancel_returns_deny(self):
        """Cancelling returns deny."""

        async def _test():
            App = _noop_client_app_class()
            app = App(
                options=_make_mock_options(), human_ticket_review=True,
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()
                result_holder: list = []

                async def _call():
                    r = await app._approve_ticket_hook(
                        {"tool_input": self._TOOL_INPUT}, None, None,
                    )
                    result_holder.append(r)

                task = loop.create_task(_call())
                await pilot.pause()
                await pilot.pause()

                app._choice_future.cancel()
                await task

                assert result_holder
                hook_out = result_holder[0]["hookSpecificOutput"]
                assert hook_out["permissionDecision"] == "deny"
                assert "interrupted" in hook_out["permissionDecisionReason"]

        _run(_test)

    def test_free_text_returns_deny(self):
        """Free-text input is treated as rejection with the text as reason."""

        async def _test():
            App = _noop_client_app_class()
            app = App(
                options=_make_mock_options(), human_ticket_review=True,
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()
                result_holder: list = []

                async def _call():
                    r = await app._approve_ticket_hook(
                        {"tool_input": self._TOOL_INPUT}, None, None,
                    )
                    result_holder.append(r)

                task = loop.create_task(_call())
                await pilot.pause()
                await pilot.pause()

                # Free-text: not a numbered option, passes through as-is
                app._choice_future.set_result("needs more detail")
                await task

                assert result_holder
                hook_out = result_holder[0]["hookSpecificOutput"]
                assert hook_out["permissionDecision"] == "deny"
                assert hook_out["permissionDecisionReason"] == "needs more detail"

        _run(_test)


# ── Ctrl+C interrupt tests ──────────────────────────────────────────────────


class TestCtrlCInterrupt:
    """action_cancel_or_quit should interrupt the SDK client and cancel futures."""

    def test_cancels_choice_future(self):
        """Ctrl+C cancels a pending choice future."""

        async def _test():
            App = _noop_client_app_class()
            app = App(options=_make_mock_options())
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                loop = asyncio.get_event_loop()
                app._choice_future = loop.create_future()
                app._query_active = True

                mock_client = AsyncMock()
                app._client = mock_client

                app.action_cancel_or_quit()
                await pilot.pause()

                assert app._choice_future.cancelled()
                mock_client.interrupt.assert_called_once()

        _run(_test)

    def test_interrupt_called_on_client(self):
        """Ctrl+C calls client.interrupt() to stop the CLI subprocess."""

        async def _test():
            App = _noop_client_app_class()
            app = App(options=_make_mock_options())
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                app._query_active = True

                mock_client = AsyncMock()
                app._client = mock_client

                app.action_cancel_or_quit()
                await pilot.pause()

                mock_client.interrupt.assert_called_once()

        _run(_test)


# ── TaskPanel tests ──────────────────────────────────────────────────────────


class TestTaskPanel:
    def test_empty_shows_placeholder(self):
        """TaskPanel with no todos shows '(no tasks)'."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield TaskPanel(id="tp")

            app = _App()
            async with app.run_test() as pilot:
                await pilot.pause()
                tp = app.query_one("#tp", TaskPanel)
                assert len(list(tp.children)) == 1

        _run(_test)

    def test_pending_items(self):
        """Pending items render with checkbox icon and task-pending class."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.widgets import Static

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield TaskPanel(id="tp")

            app = _App()
            async with app.run_test() as pilot:
                tp = app.query_one("#tp", TaskPanel)
                tp.refresh_todos([
                    {"content": "Run tests", "status": "pending", "activeForm": "Running tests"},
                ])
                await pilot.pause()
                items = [s for s in tp.query(Static) if s.has_class("task-pending")]
                assert len(items) == 1
                rendered = items[0].render().plain
                assert "\u2610" in rendered
                assert "Run tests" in rendered

        _run(_test)

    def test_in_progress_shows_active_form(self):
        """In-progress items show activeForm text, not content."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.widgets import Static

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield TaskPanel(id="tp")

            app = _App()
            async with app.run_test() as pilot:
                tp = app.query_one("#tp", TaskPanel)
                tp.refresh_todos([
                    {"content": "Build project", "status": "in_progress", "activeForm": "Building project"},
                ])
                await pilot.pause()
                items = [s for s in tp.query(Static) if s.has_class("task-in-progress")]
                assert len(items) == 1
                rendered = items[0].render().plain
                assert "\u23f3" in rendered
                assert "Building project" in rendered

        _run(_test)

    def test_completed_items(self):
        """Completed items render with checkmark and task-completed class."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.widgets import Static

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield TaskPanel(id="tp")

            app = _App()
            async with app.run_test() as pilot:
                tp = app.query_one("#tp", TaskPanel)
                tp.refresh_todos([
                    {"content": "Done task", "status": "completed", "activeForm": "Doing task"},
                ])
                await pilot.pause()
                items = [s for s in tp.query(Static) if s.has_class("task-completed")]
                assert len(items) == 1
                rendered = items[0].render().plain
                assert "\u2713" in rendered

        _run(_test)

    def test_refresh_replaces_list(self):
        """Calling refresh_todos replaces previous items."""

        async def _test():
            from textual.app import App, ComposeResult
            from textual.widgets import Static

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield TaskPanel(id="tp")

            app = _App()
            async with app.run_test() as pilot:
                tp = app.query_one("#tp", TaskPanel)
                tp.refresh_todos([
                    {"content": "A", "status": "pending", "activeForm": "A"},
                    {"content": "B", "status": "pending", "activeForm": "B"},
                ])
                await pilot.pause()
                assert len(tp.query(Static)) == 2

                tp.refresh_todos([
                    {"content": "X", "status": "completed", "activeForm": "X"},
                ])
                await pilot.pause()
                statics = tp.query(Static)
                assert len(statics) == 1
                assert "X" in statics[0].render().plain

        _run(_test)


# ── _summarize_todos tests ───────────────────────────────────────────────────


class TestSummarizeTodos:
    def test_all_completed(self):
        todos = [
            {"content": "A", "status": "completed", "activeForm": "A"},
            {"content": "B", "status": "completed", "activeForm": "B"},
        ]
        assert _summarize_todos(todos) == "2/2"

    def test_partial_with_active(self):
        todos = [
            {"content": "A", "status": "completed", "activeForm": "Doing A"},
            {"content": "B", "status": "in_progress", "activeForm": "Doing B"},
            {"content": "C", "status": "pending", "activeForm": "Doing C"},
        ]
        result = _summarize_todos(todos)
        assert "1/3" in result
        assert "Doing B" in result

    def test_empty_list(self):
        assert _summarize_todos([]) == "0/0"

    def test_long_active_form_truncated(self):
        todos = [
            {"content": "X", "status": "in_progress", "activeForm": "A" * 50},
        ]
        result = _summarize_todos(todos)
        assert "..." in result
        assert len(result) < 50


# ── _format_tool_body TodoWrite tests ────────────────────────────────────────


class TestFormatToolBodyTodoWrite:
    def test_renders_checklist(self):
        args = {"todos": [
            {"content": "Run tests", "status": "completed"},
            {"content": "Build", "status": "in_progress"},
            {"content": "Deploy", "status": "pending"},
        ]}
        result = _format_tool_body("TodoWrite", args)
        assert isinstance(result, str)
        assert "\u2713 Run tests" in result
        assert "\u23f3 Build" in result
        assert "\u2610 Deploy" in result

    def test_empty_todos_falls_through(self):
        result = _format_tool_body("TodoWrite", {"todos": []})
        # Empty list → falls through to generic JSON
        assert isinstance(result, Syntax)
