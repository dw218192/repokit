"""HTTP MCP server for multi-agent team coordination.

Started by ``agent team``; blocks until Ctrl+C.  On exit it
kills all registered agent panes (workers and reviewers — not the
orchestrator, which is user-managed).

Protocol
--------
* MCP JSON-RPC 2.0 over HTTP at ``POST /``
* Exposes two tools: ``send_message``, ``coderabbit_review``
* Internal endpoints (not MCP): ``POST /register`` and ``POST /idle``

Idle detection
--------------
The Claude Code Stop hook (``hooks/stop_hook.py``) fires each time an
agent finishes a generation turn and calls ``POST /idle`` with the
WezTerm pane ID.  A background watchdog thread tracks idle duration
and sends progressively more urgent reminders.  After
``idle_reminder_limit`` consecutive reminders the pane is killed and
the orchestrator is notified via ``send_message`` injection.

``send_message`` calls (from the agent itself) reset the idle counter
so a genuinely active agent is never killed.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any

from ..core import logger
from .coderabbit import call_review
from .wezterm import PaneSession, list_workspace

# ── Defaults (may be overridden via repo.toml [tool.agent]) ──────────────────

DEFAULT_REMINDER_INTERVAL = 120  # seconds between reminders
DEFAULT_REMINDER_LIMIT = 3       # consecutive reminders before force-kill

IDLE_REMINDER = (
    "You appear idle. Use the send_message tool to report your ticket status, "
    "or /exit if your work is complete."
)


# ── Per-pane state ────────────────────────────────────────────────────────────


@dataclass
class PaneState:
    pane_id: int
    role: str
    workstream: str
    ticket: str
    idle_since: float | None = None
    reminder_count: int = 0
    last_reminder: float | None = None


# ── MCP tool definitions ──────────────────────────────────────────────────────

_TOOLS = [
    {
        "name": "coderabbit_review",
        "description": (
            "Run the CodeRabbit CLI to review code changes in a git worktree. "
            "Returns plain-text reviewer feedback. "
            "If the CLI is not installed or not authenticated, returns an error message "
            "instructing you to fall back to manual review."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "worktree_path": {
                    "type": "string",
                    "description": "Absolute path to the git worktree whose changes should be reviewed.",
                },
                "type": {
                    "type": "string",
                    "enum": ["committed", "uncommitted", "all"],
                    "default": "committed",
                    "description": "Which changes to review: 'committed' (default), 'uncommitted', or 'all'.",
                },
            },
            "required": ["worktree_path"],
        },
    },
    {
        "name": "send_message",
        "description": (
            "Relay a status message to the orchestrator. "
            "Use this to report ticket status (verify/closed/open) when your work is done. "
            "Calling this tool resets your idle timer. "
            "Set done=true on your final call — the server will kill your pane automatically "
            "so you do not need to /exit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Target: 'orchestrator' (default) or numeric pane_id",
                    "default": "orchestrator",
                },
                "workstream": {
                    "type": "string",
                    "description": "Workstream ID",
                },
                "ticket": {
                    "type": "string",
                    "description": "Your ticket ID — resets your idle reminder counter",
                },
                "message": {
                    "type": "string",
                    "description": "Message text, e.g. 'TICKET G1_1: status=verify notes=...'",
                },
                "done": {
                    "type": "boolean",
                    "description": (
                        "Set true on your final send_message call. "
                        "The server will terminate your pane a few seconds after delivery "
                        "so you do not need to type /exit."
                    ),
                    "default": False,
                },
            },
            "required": ["workstream", "message"],
        },
    }
]

# Maximum HTTP request body size (1 MiB) to prevent memory exhaustion.
_MAX_BODY_SIZE = 1_048_576

# Seconds to wait after done=true before killing the pane, giving Claude Code
# time to display the "Message sent." tool result before the pane disappears.
_DONE_CLEANUP_DELAY = 5


# ── HTTP server ───────────────────────────────────────────────────────────────


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ── TeamMCPServer ─────────────────────────────────────────────────────────────


class TeamMCPServer:
    """HTTP MCP server for one workstream team session.

    Lifecycle: call ``run()`` which blocks until Ctrl+C, then kills all
    registered panes and exits.
    """

    def __init__(
        self,
        workstream: str,
        port: int,
        reminder_interval: int = DEFAULT_REMINDER_INTERVAL,
        reminder_limit: int = DEFAULT_REMINDER_LIMIT,
    ) -> None:
        self.workstream = workstream
        self.port = port
        self.reminder_interval = reminder_interval
        self.reminder_limit = reminder_limit
        self._panes: dict[int, PaneState] = {}              # pane_id → state
        self._key_map: dict[tuple[str, str], int] = {}      # (workstream, ticket) → pane_id
        self._lock = threading.Lock()
        self._http: _ThreadingHTTPServer | None = None

    # ── Registration ─────────────────────────────────────────────────────────

    def register_pane(
        self, pane_id: int, role: str, workstream: str, ticket: str
    ) -> None:
        with self._lock:
            state = PaneState(
                pane_id=pane_id, role=role, workstream=workstream, ticket=ticket
            )
            self._panes[pane_id] = state
            self._key_map[(workstream, ticket)] = pane_id
        logger.info(f"Registered pane {pane_id} → {role} {workstream}/{ticket}")

    def deregister_pane(self, pane_id: int) -> None:
        with self._lock:
            state = self._panes.pop(pane_id, None)
            if state:
                self._key_map.pop((state.workstream, state.ticket), None)

    # ── Idle signalling ───────────────────────────────────────────────────────

    def notify_idle(self, pane_id: int) -> None:
        """Called by the Stop hook when an agent finishes a generation turn."""
        with self._lock:
            state = self._panes.get(pane_id)
            if state and state.idle_since is None:
                state.idle_since = time.monotonic()

    def notify_active(self, workstream: str, ticket: str) -> None:
        """Reset idle state when an agent calls send_message (active work)."""
        with self._lock:
            pane_id = self._key_map.get((workstream, ticket))
            if pane_id is not None:
                state = self._panes[pane_id]
                state.idle_since = None
                state.reminder_count = 0
                state.last_reminder = None

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def _watchdog_loop(self) -> None:
        while True:
            time.sleep(10)
            self._watchdog_tick()

    def _watchdog_tick(self) -> None:
        now = time.monotonic()
        # Collect actions under lock; execute outside to avoid deadlocks.
        to_remind: list[tuple[int, int]] = []   # (pane_id, reminder_count)
        to_kill: list[tuple[int, str, str, int]] = []  # (pane_id, workstream, ticket, count)

        with self._lock:
            for pane_id, state in list(self._panes.items()):
                if state.idle_since is None:
                    continue
                elapsed_since_last = now - (
                    state.last_reminder if state.last_reminder is not None
                    else state.idle_since
                )
                if elapsed_since_last < self.reminder_interval:
                    continue
                state.reminder_count += 1
                state.last_reminder = now
                if state.reminder_count >= self.reminder_limit:
                    to_kill.append(
                        (pane_id, state.workstream, state.ticket, state.reminder_count)
                    )
                else:
                    to_remind.append((pane_id, state.reminder_count))

        for pane_id, count in to_remind:
            try:
                PaneSession(pane_id).compose_input() \
                    .keys("\x1b").pause(0.3) \
                    .text(IDLE_REMINDER).pause(0.3) \
                    .keys("\r").send()
                logger.info(f"Idle reminder #{count} → pane {pane_id}")
            except Exception as exc:
                logger.debug(f"Reminder send failed (pane {pane_id}): {exc}")

        for pane_id, workstream, ticket, count in to_kill:
            self._kill_stalled(pane_id, workstream, ticket, count)

    def _kill_stalled(
        self, pane_id: int, workstream: str, ticket: str, reminder_count: int
    ) -> None:
        """Force-kill a stalled pane and notify the orchestrator."""
        with self._lock:
            if pane_id not in self._panes:
                return  # Already gone (race with send_message reset)
        logger.warning(
            f"Killing stalled pane {pane_id} ({workstream}/{ticket}) "
            f"after {reminder_count} idle reminders"
        )
        try:
            PaneSession(pane_id).kill()
        except Exception as exc:
            logger.debug(f"Kill pane {pane_id} failed: {exc}")
        self.deregister_pane(pane_id)

        msg = (
            f"TICKET {ticket}: status=open "
            f"notes='agent killed after {reminder_count} idle reminders "
            f"— possible context rot or non-compliance'"
        )
        try:
            panes = list_workspace(workstream)
            if panes:
                PaneSession(panes[0]["pane_id"]).compose_input() \
                    .keys("\x1b").pause(0.3) \
                    .text(msg).pause(0.3) \
                    .keys("\r").send()
        except Exception as exc:
            logger.debug(f"Orchestrator notify failed: {exc}")

    # ── Tool dispatch ─────────────────────────────────────────────────────────

    def _call_coderabbit_review(self, args: dict) -> dict:
        """Run ``coderabbit review --plain`` in the given worktree and return the output."""
        worktree_path = args.get("worktree_path", "").strip()
        if not worktree_path:
            return {"isError": True, "text": "worktree_path is required"}
        return call_review(args, logger=logger)

    def _cleanup_pane(self, pane_id: int) -> None:
        """Kill and deregister a pane after it signals done via send_message."""
        with self._lock:
            if pane_id not in self._panes:
                return  # Already gone (watchdog beat us, or pane self-exited)
        logger.info(f"Auto-cleanup: killing pane {pane_id}")
        try:
            PaneSession(pane_id).kill()
        except Exception as exc:
            logger.debug(f"Cleanup kill pane {pane_id} failed: {exc}")
        self.deregister_pane(pane_id)

    def _call_send_message(self, args: dict) -> dict:
        target = args.get("target", "orchestrator")
        workstream = args.get("workstream", self.workstream)
        ticket = args.get("ticket", "")
        message = args.get("message", "")
        done = args.get("done", False)

        if ticket:
            self.notify_active(workstream, ticket)

        # Resolve caller pane before delivering (while _key_map is still intact).
        caller_pane_id: int | None = None
        if done and ticket:
            with self._lock:
                caller_pane_id = self._key_map.get((workstream, ticket))

        panes = list_workspace(workstream)
        if not panes:
            return {"isError": True, "text": f"No panes in workspace '{workstream}'"}

        target_pane = None
        if target == "orchestrator":
            target_pane = panes[0]
        else:
            try:
                tid = int(target)
                target_pane = next((p for p in panes if p.get("pane_id") == tid), None)
            except ValueError:
                return {"isError": True, "text": f"Invalid target: {target!r}"}

        if target_pane is None:
            return {"isError": True, "text": f"Target '{target}' not found in '{workstream}'"}

        PaneSession(target_pane["pane_id"]).compose_input() \
            .keys("\x1b").pause(0.3) \
            .text(message).pause(0.3) \
            .keys("\r").send()
        logger.info(f"send_message → pane {target_pane['pane_id']}: {message!r}")

        # Schedule pane cleanup after done=true so the caller's pane is GC'd
        # once Claude Code has had time to display the "Message sent." result.
        if caller_pane_id is not None:
            threading.Timer(
                _DONE_CLEANUP_DELAY, self._cleanup_pane, args=(caller_pane_id,)
            ).start()
            logger.info(
                f"Pane {caller_pane_id} scheduled for auto-cleanup in {_DONE_CLEANUP_DELAY}s"
            )

        return {"text": "Message sent. Your pane will be cleaned up automatically." if caller_pane_id else "Message sent."}

    # ── HTTP / MCP request handler ────────────────────────────────────────────

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # silence default stderr logging
                logger.debug("MCP HTTP: " + fmt % args)

            class _BodyTooLarge(Exception):
                pass

            def _body(self) -> bytes:
                length = int(self.headers.get("Content-Length", 0))
                if length > _MAX_BODY_SIZE:
                    raise _Handler._BodyTooLarge(f"Body too large: {length} > {_MAX_BODY_SIZE}")
                return self.rfile.read(length) if length else b""

            def _json(self, data: Any, status: int = 200) -> None:
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _empty(self, status: int = 202) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                path = self.path.split("?")[0]

                try:
                    raw_body = self._body()
                except _Handler._BodyTooLarge:
                    self._json({"error": "request body too large"}, 413)
                    return

                # ── Internal: pane registration ──────────────────────────────
                if path == "/register":
                    try:
                        body = json.loads(raw_body)
                        server.register_pane(
                            pane_id=int(body["pane_id"]),
                            role=body["role"],
                            workstream=body["workstream"],
                            ticket=body["ticket"],
                        )
                    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                        self._json({"error": f"bad request: {exc}"}, 400)
                        return
                    self._json({"ok": True})
                    return

                # ── Internal: idle signal from Stop hook ─────────────────────
                if path == "/idle":
                    try:
                        body = json.loads(raw_body)
                        server.notify_idle(int(body["pane_id"]))
                    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                        self._json({"error": f"bad request: {exc}"}, 400)
                        return
                    self._empty(200)
                    return

                # ── MCP JSON-RPC ─────────────────────────────────────────────
                if path in ("/", "/mcp"):
                    try:
                        req = json.loads(raw_body)
                    except json.JSONDecodeError:
                        self._json({"error": "invalid JSON"}, 400)
                        return
                    self._dispatch_mcp(req)
                    return

                self._json({"error": "not found"}, 404)

            def _dispatch_mcp(self, req: dict) -> None:
                method = req.get("method", "")
                req_id = req.get("id")
                session_id = self.headers.get("Mcp-Session-Id") or str(uuid.uuid4())

                # Notifications (no id) → 202
                if req_id is None:
                    self._empty(202)
                    return

                if method == "initialize":
                    result = {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "repokit-team", "version": "0.1"},
                    }
                    body = json.dumps(
                        {"jsonrpc": "2.0", "id": req_id, "result": result}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", session_id)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if method == "ping":
                    self._json({"jsonrpc": "2.0", "id": req_id, "result": {}})
                    return

                if method == "tools/list":
                    self._json({
                        "jsonrpc": "2.0", "id": req_id,
                        "result": {"tools": _TOOLS},
                    })
                    return

                if method == "tools/call":
                    params = req.get("params", {})
                    name = params.get("name", "")
                    args = params.get("arguments", {})
                    if name == "send_message":
                        outcome = server._call_send_message(args)
                    elif name == "coderabbit_review":
                        outcome = server._call_coderabbit_review(args)
                    else:
                        outcome = {"isError": True, "text": f"Unknown tool: {name!r}"}
                    result = {
                        "content": [{"type": "text", "text": outcome["text"]}],
                        **({"isError": True} if outcome.get("isError") else {}),
                    }
                    self._json({"jsonrpc": "2.0", "id": req_id, "result": result})
                    return

                # Unknown method with id → method-not-found
                self._json({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })

        return _Handler

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start HTTP server and watchdog; block until Ctrl+C."""
        handler = self._make_handler()
        self._http = _ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        logger.info(f"Team MCP server on http://127.0.0.1:{self.port} (workstream={self.workstream!r})")
        logger.info("Press Ctrl+C to stop the team session and kill all agent panes.")

        watchdog = threading.Thread(target=self._watchdog_loop, daemon=True, name="mcp-watchdog")
        watchdog.start()

        try:
            self._http.serve_forever()
        except KeyboardInterrupt:
            logger.info("Stopping team session…")
        finally:
            self._http.shutdown()
            self._shutdown_all_panes()

    def _shutdown_all_panes(self) -> None:
        with self._lock:
            pane_ids = list(self._panes.keys())
        for pane_id in pane_ids:
            try:
                PaneSession(pane_id).kill()
                logger.info(f"Killed pane {pane_id}")
            except Exception as exc:
                logger.debug("Failed to kill pane %d: %s", pane_id, exc)
        logger.info(f"Workstream '{self.workstream}' session ended.")


# ── Utility ───────────────────────────────────────────────────────────────────


def find_free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]
