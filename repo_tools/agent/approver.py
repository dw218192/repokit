"""Generic auto-approval daemon for agent permission prompts.

The ``AutoApprover`` polls a :class:`PaneSession` and delegates prompt
detection to the :class:`AgentCLITool` that owns the session.
"""

from __future__ import annotations

import re
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import bashlex
import bashlex.ast

from ..core import logger

if TYPE_CHECKING:
    from .runner import AgentCLITool, ToolRequest
    from .wezterm import PaneSession


_QUOTED_HEREDOC_RE = re.compile(r"<<['\"](\w+)['\"]")
_OPERATOR_RE = re.compile(r"\s*(?:&&|\|\|?|;)\s*")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_]\w*=")


# ------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------


@dataclass
class Rule:
    name: str
    patterns: list[re.Pattern[str]]
    reason: str | None = None
    dir: str | None = None  # "project_root" or "!project_root"


@dataclass
class RuleSet:
    default_reason: str
    deny: list[Rule] = field(default_factory=list)
    allow: list[Rule] = field(default_factory=list)


def load_rules(path: Path) -> RuleSet:
    """Parse a TOML rules file into a :class:`RuleSet`."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    deny = [
        Rule(
            name=r["name"],
            patterns=[re.compile(p) for p in r["patterns"]],
            reason=r.get("reason"),
            dir=r.get("dir"),
        )
        for r in data.get("deny", [])
    ]
    allow = [
        Rule(
            name=r["name"],
            patterns=[re.compile(p) for p in r["patterns"]],
            reason=r.get("reason"),
            dir=r.get("dir"),
        )
        for r in data.get("allow", [])
    ]
    return RuleSet(
        default_reason=data.get("default_reason", "try another approach"),
        deny=deny,
        allow=allow,
    )


# ------------------------------------------------------------------
# Command parsing
# ------------------------------------------------------------------


def _has_wrap_artifacts(commands: list[str]) -> bool:
    for cmd in commands:
        first = cmd.split()[0]
        if first.startswith("-") or len(first) <= 1:
            return True
    return False


def _try_bashlex(text: str) -> list[str] | None:
    normalized = _QUOTED_HEREDOC_RE.sub(r"<<\1", text)
    commands: list[str] = []
    try:
        for node in bashlex.parse(normalized):
            _walk_node(node, commands)
    except Exception:
        return None
    if not commands or _has_wrap_artifacts(commands):
        return None
    return commands


def _extract_commands(command: str) -> list[str]:
    """Parse a shell command and return each command segment."""
    result = _try_bashlex(command)
    if result is not None:
        return result

    unwrapped = command.replace("\n", "")
    if unwrapped != command:
        result = _try_bashlex(unwrapped)
        if result is not None:
            return result

    commands: list[str] = []
    flat = " ".join(unwrapped.split())
    for segment in _OPERATOR_RE.split(flat):
        segment = segment.strip()
        if not segment:
            continue
        words = segment.split()
        while words and _ASSIGNMENT_RE.match(words[0]):
            words.pop(0)
        if words:
            commands.append(" ".join(words))

    return commands


def _walk_node(node: bashlex.ast.node, out: list[str]) -> None:
    kind = node.kind
    if kind == "command":
        words = []
        for part in node.parts:
            if part.kind == "word":
                words.append(part.word)
        if words:
            out.append(" ".join(words))
    elif kind in ("list", "pipeline", "compound"):
        for part in node.parts:
            if hasattr(part, "kind"):
                _walk_node(part, out)
    elif kind in ("if", "for", "while", "until"):
        for attr in ("parts", "list", "body"):
            for child in getattr(node, attr, []):
                if hasattr(child, "kind"):
                    _walk_node(child, out)


# ------------------------------------------------------------------
# AutoApprover
# ------------------------------------------------------------------


class AutoApprover:
    """Daemon thread: poll a pane session, detect prompts via the
    :class:`AgentCLITool`, approve Bash commands that pass the rule set."""

    POLL_INTERVAL = 0.8
    COOLDOWN = 2.0

    def __init__(
        self,
        backend: AgentCLITool,
        session: PaneSession,
        rules_path: Path,
        project_root: Path | None = None,
    ) -> None:
        self._backend = backend
        self._session = session
        self._rules_path = rules_path
        self._rules = load_rules(rules_path)
        self._project_root = project_root.resolve() if project_root else None
        self._cwd = self._project_root
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_hash: int = 0
        n = len(self._rules.deny) + len(self._rules.allow)
        logger.info(f"Auto-approver loaded {n} rule groups from {rules_path.name}")

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="auto-approve"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                text = self._session.get_text()
                h = hash(text)
                if text and h != self._last_hash:
                    self._last_hash = h
                    if self._check_and_respond(text):
                        self._stop.wait(self.COOLDOWN)
                        continue
            except Exception as exc:
                logger.debug(f"Auto-approve poll error: {exc}")
            self._stop.wait(self.POLL_INTERVAL)

    def _check_and_respond(self, text: str) -> bool:
        req = self._backend.detect_prompt(text)
        if req is None:
            return False

        allowed, reason = self._check_request(req)
        if allowed:
            logger.info(f"Auto-approving: {req}")
            self._session.send_keys(self._backend.approve_key)
            return True

        logger.warning(f"Denying: {req} ({reason})")
        self._deny(reason)
        return True

    def _deny(self, reason: str) -> None:
        self._session.send_keys(self._backend.deny_key)
        self._wait_for_prompt_clear()
        msg = (
            f"This command was denied: {reason}. "
            f"See @{self._rules_path} for the rules"
        )
        self._backend.send_text(self._session, msg)

    def _wait_for_prompt_clear(self, timeout: float = 5.0) -> None:
        interval = 0.3
        elapsed = 0.0
        while elapsed < timeout:
            self._stop.wait(interval)
            elapsed += interval
            text = self._session.get_text()
            if not self._backend.detect_prompt(text):
                self._stop.wait(0.5)
                return
        logger.debug("Timed out waiting for prompt to clear")

    def _check_request(self, req: ToolRequest) -> tuple[bool, str]:
        if req.tool != "Bash":
            return True, ""

        if not req.command:
            return False, self._rules.default_reason

        commands = _extract_commands(req.command)
        if not commands:
            return False, self._rules.default_reason

        for rule in self._rules.deny:
            if any(
                any(pat.search(cmd) for pat in rule.patterns)
                for cmd in commands
            ):
                if self._check_constraints(rule):
                    return False, rule.reason or self._rules.default_reason

        for cmd in commands:
            matched_rule = None
            for rule in self._rules.allow:
                if any(pat.search(cmd) for pat in rule.patterns):
                    matched_rule = rule
                    break
            if matched_rule is None:
                return False, self._rules.default_reason
            if not self._check_constraints(matched_rule):
                return False, matched_rule.reason or self._rules.default_reason

        return True, ""

    def _check_constraints(self, rule: Rule) -> bool:
        if rule.dir is None:
            return True
        return self._check_dir_constraint(rule.dir)

    def _check_dir_constraint(self, dir_spec: str) -> bool:
        negate = dir_spec.startswith("!")
        name = dir_spec.lstrip("!")

        if name == "project_root":
            if self._project_root is None or self._cwd is None:
                return True
            try:
                inside = self._cwd.is_relative_to(self._project_root)
            except (ValueError, TypeError):
                inside = False
            return not inside if negate else inside

        return True
