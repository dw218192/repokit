"""WezTerm CLI integration for agent spawning and pane management."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from ..core import logger


def ensure_installed() -> str:
    """Return path to wezterm, or exit with error.

    Must be run from inside a WezTerm terminal (``WEZTERM_PANE`` set)
    so that ``wezterm cli`` can reach the mux socket.
    """
    path = shutil.which("wezterm")
    if not path:
        logger.error(
            "WezTerm is required but not found on PATH.\n"
            "  Install from: https://wezfurlong.org/wezterm/"
        )
        raise SystemExit(1)
    if not os.environ.get("WEZTERM_PANE"):
        logger.error("This command must be run from inside a WezTerm terminal.")
        raise SystemExit(1)
    return path


def _run_cli(
    *args: str, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    wez = ensure_installed()
    result = subprocess.run(
        [wez, "cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=input_text,
    )
    if result.returncode != 0:
        logger.debug(f"wezterm cli {args[0]} failed: {result.stderr.strip()}")
    return result


def spawn_in_workspace(
    cmd: list[str], workspace: str, cwd: str | None = None,
) -> PaneSession | None:
    """Spawn a new window in the given workspace and return its PaneSession."""
    cli_args = ["spawn", "--new-window", "--workspace", workspace]
    if cwd:
        cli_args.extend(["--cwd", cwd])
    cli_args.append("--")
    cli_args.extend(cmd)
    result = _run_cli(*cli_args)
    if result.returncode != 0:
        return None
    try:
        return PaneSession(int(result.stdout.strip()))
    except ValueError:
        logger.warning(f"Unexpected spawn output: {result.stdout.strip()}")
        return None


def list_workspace(workspace: str) -> list[dict]:
    """Return list of pane info dicts for the given workspace."""
    result = _run_cli("list", "--format", "json")
    if result.returncode != 0:
        return []
    try:
        panes = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [p for p in panes if p.get("workspace") == workspace]


def kill_workspace(workspace: str) -> int:
    """Kill all panes in a workspace. Return count of killed panes."""
    panes = list_workspace(workspace)
    killed = 0
    for p in panes:
        pid = p.get("pane_id")
        if pid is not None:
            result = _run_cli("kill-pane", "--pane-id", str(pid))
            if result.returncode == 0:
                killed += 1
    return killed


class _SendBuilder:
    """Fluent builder for composing a sequence of pane I/O steps."""

    def __init__(self, session: PaneSession) -> None:
        self._session = session
        self._steps: list[tuple[str, object]] = []

    def keys(self, k: str) -> _SendBuilder:
        self._steps.append(("keys", k))
        return self

    def text(self, t: str) -> _SendBuilder:
        self._steps.append(("text", t))
        return self

    def pause(self, seconds: float) -> _SendBuilder:
        self._steps.append(("pause", seconds))
        return self

    def send(self) -> bool:
        ok = True
        for kind, value in self._steps:
            if kind == "keys":
                if not self._session.send_keys(value):
                    logger.warning("send_keys failed for pane %s: %r", self._session.pane_id, value)
                    ok = False
            elif kind == "text":
                if not self._session.send_text(value):
                    logger.warning("send_text failed for pane %s: %r", self._session.pane_id, value)
                    ok = False
            elif kind == "pause":
                time.sleep(value)
        return ok


class PaneSession:
    """A managed WezTerm pane with get/send/alive operations."""

    def __init__(self, pane_id: int) -> None:
        self.pane_id = pane_id

    def compose_input(self) -> _SendBuilder:
        """Return a fluent builder for composing pane input sequences."""
        return _SendBuilder(self)

    def get_text(self) -> str:
        result = _run_cli("get-text", "--pane-id", str(self.pane_id))
        if result.returncode != 0:
            return ""
        return result.stdout

    def send_keys(self, keys: str) -> bool:
        result = _run_cli(
            "send-text", "--pane-id", str(self.pane_id),
            "--no-paste", input_text=keys,
        )
        return result.returncode == 0

    def send_text(self, text: str) -> bool:
        result = _run_cli(
            "send-text", "--pane-id", str(self.pane_id),
            input_text=text,
        )
        return result.returncode == 0

    def is_alive(self) -> bool:
        result = _run_cli("list", "--format", "json")
        if result.returncode != 0:
            return False
        try:
            panes = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        return any(p.get("pane_id") == self.pane_id for p in panes)

    def kill(self) -> None:
        _run_cli("kill-pane", "--pane-id", str(self.pane_id))

    @classmethod
    def spawn(cls, cmd: list[str], cwd: str | None = None) -> PaneSession | None:
        cli_args = ["spawn", "--new-window"]
        if cwd:
            cli_args.extend(["--cwd", cwd])
        cli_args.append("--")
        cli_args.extend(cmd)
        result = _run_cli(*cli_args)
        if result.returncode != 0:
            return None
        try:
            return cls(int(result.stdout.strip()))
        except ValueError:
            logger.warning(f"Unexpected spawn output: {result.stdout.strip()}")
            return None
