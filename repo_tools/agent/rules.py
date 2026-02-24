"""Rule-based permission checking for agent Bash commands.

Provides :func:`load_rules` to parse a TOML rule file and
:func:`check_command` to evaluate a shell command against the rule set.
"""

from __future__ import annotations

import re

import bashlex
import bashlex.errors

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


# ------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------


@dataclass
class Rule:
    name: str
    patterns: list[re.Pattern[str]]
    reason: str | None = None
    dir: str | None = None  # "project_root" or "!project_root"
    roles: list[str] | None = None  # if set, rule only applies to listed roles


@dataclass
class RuleSet:
    default_reason: str
    deny: list[Rule] = field(default_factory=list)
    allow: list[Rule] = field(default_factory=list)


def _compile_rule_list(entries: list, section: str, role: str | None = None) -> list[Rule]:
    """Validate and compile a list of raw rule dicts into :class:`Rule` objects.

    Each rule must have at least one of ``commands`` or ``patterns``:

    * ``commands`` — list of literal command names; each is compiled to
      ``^<re.escape(name)>\\b`` so that ``"rm"`` matches ``rm -rf`` but
      not ``rmdir``.
    * ``patterns`` — list of raw regex strings for complex matches.

    Both may be present on the same rule (their compiled patterns are merged).

    Raises :class:`ValueError` with the offending section index, rule name,
    and pattern when a required key is missing or a regex is invalid.

    When *role* is given, rules with a ``roles`` list that does not include
    *role* are silently skipped.
    """
    rules: list[Rule] = []
    for i, r in enumerate(entries):
        if "name" not in r:
            raise ValueError(f"{section}[{i}]: missing required key 'name'")
        name = r["name"]
        has_commands = "commands" in r
        has_patterns = "patterns" in r
        if not has_commands and not has_patterns:
            raise ValueError(f"{section}[{i}] ({name!r}): must have 'commands' and/or 'patterns'")
        if has_patterns:
            if isinstance(r["patterns"], str) or not isinstance(r["patterns"], (list, tuple)):
                raise ValueError(f"{section}[{i}] ({name!r}): 'patterns' must be a list, got {type(r['patterns']).__name__}")
        if has_commands:
            if isinstance(r["commands"], str) or not isinstance(r["commands"], (list, tuple)):
                raise ValueError(f"{section}[{i}] ({name!r}): 'commands' must be a list, got {type(r['commands']).__name__}")
        rule_roles = r.get("roles")
        if rule_roles is not None and role not in rule_roles:
            continue  # rule doesn't apply to this role
        compiled: list[re.Pattern[str]] = []
        for cmd_name in r.get("commands", []):
            compiled.append(re.compile(rf"^{re.escape(cmd_name)}\b"))
        for pat in r.get("patterns", []):
            try:
                compiled.append(re.compile(pat))
            except re.error as exc:
                raise ValueError(
                    f"{section}[{i}] ({name!r}): invalid regex {pat!r}: {exc}"
                ) from exc
        rules.append(Rule(name=name, patterns=compiled, reason=r.get("reason"), dir=r.get("dir"), roles=rule_roles))
    return rules


def load_rules(path: Path, role: str | None = None) -> RuleSet:
    """Parse a TOML rules file into a :class:`RuleSet`.

    When *role* is given, rules with a ``roles`` restriction that excludes
    *role* are omitted from the returned :class:`RuleSet`.
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return RuleSet(
        default_reason=data.get("default_reason", "try another approach"),
        deny=_compile_rule_list(data.get("deny", []), "deny", role=role),
        allow=_compile_rule_list(data.get("allow", []), "allow", role=role),
    )


# ------------------------------------------------------------------
# Command parsing
# ------------------------------------------------------------------

_OPERATOR_RE = re.compile(r"\s*(?:&&|\|\|?|;)\s*")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_]\w*=")


def _extract_commands_regex(command: str) -> list[str]:
    """Regex-based fallback for ``_extract_commands``."""
    commands: list[str] = []
    flat = " ".join(command.split())
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


def _walk_bashlex(nodes: list, command: str) -> list[str]:
    """Recursively collect command strings from a bashlex AST.

    Leading ``VAR=value`` assignments in each command node are stripped
    using the AST word positions, so quoted values with spaces
    (e.g. ``FOO="hello world" cmd``) are handled correctly.
    """
    commands: list[str] = []
    for node in nodes:
        if node.kind == "command":
            # Find the first word that isn't a VAR=… assignment
            start = None
            for part in node.parts:
                text = command[part.pos[0]:part.pos[1]]
                if start is None and _ASSIGNMENT_RE.match(text):
                    continue
                if start is None:
                    start = part.pos[0]
            if start is not None:
                commands.append(command[start:node.pos[1]])
        elif hasattr(node, "parts"):
            commands.extend(_walk_bashlex(node.parts, command))
    return commands


def _extract_commands(command: str) -> list[str]:
    """Split a compound shell command into individual segments.

    Uses ``bashlex`` AST parsing to correctly handle quoted operators,
    subshells, and process substitution.  Falls back to regex splitting
    on parse errors.
    """
    if not command or command.isspace():
        return []
    try:
        parts = bashlex.parse(command)
        return _walk_bashlex(parts, command)
    except bashlex.errors.ParsingError:
        # Fall back to regex on any parse error
        pass
    return _extract_commands_regex(command)


# ------------------------------------------------------------------
# Permission check
# ------------------------------------------------------------------


def _check_dir_constraint(dir_spec: str, project_root: Path | None, cwd: Path | None) -> bool:
    negate = dir_spec.startswith("!")
    name = dir_spec.lstrip("!")

    if name == "project_root":
        if project_root is None or cwd is None:
            return True
        try:
            inside = cwd.is_relative_to(project_root)
        except (ValueError, TypeError):
            inside = False
        return not inside if negate else inside

    raise ValueError(f"Unknown dir_spec: {name!r}")


def check_command(
    command: str | None,
    rules: RuleSet,
    project_root: Path | None = None,
    cwd: Path | None = None,
) -> tuple[bool, str]:
    """Check a Bash command against a rule set.

    Returns ``(allowed, reason)``.  When allowed, reason is ``""``.
    """
    if not command:
        return False, rules.default_reason

    commands = _extract_commands(command)
    if not commands:
        return False, rules.default_reason

    for rule in rules.deny:
        if any(
            any(pat.search(cmd) for pat in rule.patterns)
            for cmd in commands
        ):
            if _check_dir_constraint(rule.dir, project_root, cwd) if rule.dir else True:
                return False, rule.reason or rules.default_reason

    for cmd in commands:
        matched_rule = None
        for rule in rules.allow:
            if any(pat.search(cmd) for pat in rule.patterns):
                matched_rule = rule
                break
        if matched_rule is None:
            return False, rules.default_reason
        if matched_rule.dir and not _check_dir_constraint(matched_rule.dir, project_root, cwd):
            return False, matched_rule.reason or rules.default_reason

    return True, ""
