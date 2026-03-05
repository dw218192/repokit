"""Tests for the Textual TUI — QueueBar visibility, PromptInput, ToolGroup."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repo_tools.agent.tui import (
    AgentApp,
    MarkdownMessage,
    PromptInput,
    QueueBar,
    StatusBar,
    ToolGroup,
    UserMessage,
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


# ── ToolGroup tests ──────────────────────────────────────────────────────────


class TestToolGroup:
    def test_title_updates_with_tool_count(self):
        """Title reflects number of tools and pending count."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolGroup(id="tg")

            app = _App()
            async with app.run_test() as pilot:
                tg = app.query_one("#tg", ToolGroup)
                tg.add_tool("t1", "Read")
                await pilot.pause()
                assert "1" in str(tg.title)
                assert "running" in str(tg.title).lower()

                tg.add_tool("t2", "Edit")
                await pilot.pause()
                assert "2" in str(tg.title)

                tg.set_result("t1", "ok")
                await pilot.pause()
                assert "1 running" in str(tg.title).lower()

                tg.set_result("t2", "ok")
                await pilot.pause()
                assert "running" not in str(tg.title).lower()
                assert "2 tools" in str(tg.title)

        _run(_test)

    def test_error_result_tracked(self):
        """Error results are stored with is_error flag."""

        async def _test():
            from textual.app import App, ComposeResult

            class _App(App):
                def compose(self) -> ComposeResult:
                    yield ToolGroup(id="tg")

            app = _App()
            async with app.run_test() as pilot:
                tg = app.query_one("#tg", ToolGroup)
                tg.add_tool("t1", "Bash")
                tg.set_result("t1", "command failed", is_error=True)
                await pilot.pause()
                assert tg._tools["t1"][2] is True  # is_error

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
