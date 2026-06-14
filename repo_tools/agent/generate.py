"""Standalone generation layer — emit the agent-config surface.

repokit *generates* an agent-config surface from hand-authored sources; it runs
no servers (ADR-1, `docs/spec/generation.md`). This module is that generator,
lifted out of the (dying) subprocess backend so generation is a first-class,
static emission rather than a side effect of launching a headless `claude`.

Model
-----
Each emitted file is an :class:`Artifact`: a target path (relative to the
project root), a render function producing its full content deterministically
from pinned inputs, and a merge policy. ``_managed/manifest.json`` is a local
build cache recording, per target, the sources, the rendered-content hash, and
the framework version. On each run the generator re-renders every artifact and
rewrites only those that are missing / stale / hand-edited / version-bumped
(GEN-2) — so regeneration is cheap and deterministic, with no git hooks.

Merge boundary (GEN-5): repokit owns each file it generates *wholesale* — the
merge boundary is between files, never inside one — **except** ``.mcp.json``,
which is merged in-file (repokit-generated server names win; foreign servers are
preserved).

Adoption guard (GEN-3): a whole-file-owned target that already exists but is
*not* manifest-tracked is refused, not clobbered — the caller is told how to
migrate it (e.g. a hand-authored ``claude.md`` → ``agents.md``).

This module emits no Claude-specific assumption into its core: artifacts are
just (path, content) pairs, so a Codex target set can be added later without
touching the engine (GEN-4).
"""

from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from ..core import get_project_tool_dirs, posix_path


# ── Context ──────────────────────────────────────────────────────────


@dataclass
class GenContext:
    """Inputs a render function may read. Deterministic from pinned sources."""

    project_root: Path
    framework_root: Path
    framework_version: str
    config: dict
    python_exe: str  # posix path to the interpreter that runs the MCP servers/hooks


class MergePolicy(Enum):
    OWNED = "owned"        # repokit owns the whole file; overwrite (with adoption guard)
    IN_FILE = "in_file"    # merge into an existing file (only .mcp.json)


@dataclass
class Artifact:
    target: str                              # path relative to project root
    sources: list[str]                       # human-readable source ids (manifest)
    render: Callable[[GenContext], str]      # full file content
    policy: MergePolicy = MergePolicy.OWNED
    # For OWNED artifacts that may pre-exist un-managed, how to migrate (adoption guard).
    adopt_hint: str | None = None


@dataclass
class GenResult:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)  # (target, reason)

    @property
    def ok(self) -> bool:
        return not self.refused


# ── Framework version ────────────────────────────────────────────────


def framework_version(framework_root: Path) -> str:
    """Read ``version`` from the framework's pyproject.toml (the pinned input)."""
    pyproject = framework_root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


# ── Manifest (build cache) ───────────────────────────────────────────


def _manifest_path(framework_root: Path) -> Path:
    return framework_root / "_managed" / "manifest.json"


def _load_manifest(framework_root: Path) -> dict:
    path = _manifest_path(framework_root)
    if not path.is_file():
        return {"framework_version": None, "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"framework_version": None, "files": {}}


def _save_manifest(framework_root: Path, manifest: dict) -> None:
    path = _manifest_path(framework_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _content_hash(framework_version: str, content: str) -> str:
    """Hash folds in the framework version so a bump triggers regen (GEN-2)."""
    h = hashlib.sha256()
    h.update(framework_version.encode("utf-8"))
    h.update(b"\0")
    h.update(content.encode("utf-8"))
    return "sha256:" + h.hexdigest()


# ── Engine ───────────────────────────────────────────────────────────


def _needs_write(
    target_path: Path,
    rendered: str,
    new_hash: str,
    entry: dict | None,
    framework_version: str,
) -> bool:
    """Regenerate when missing / untracked / version-bumped / stale / hand-edited."""
    if not target_path.exists():
        return True
    if entry is None:
        return True
    if entry.get("framework_version") != framework_version:
        return True
    # On-disk content differs from a fresh render → stale (sources changed) or
    # hand-edited; either way the deterministic render wins.
    try:
        on_disk = target_path.read_text(encoding="utf-8")
    except OSError:
        return True
    return _content_hash(framework_version, on_disk) != new_hash


def generate(ctx: GenContext, artifacts: list[Artifact] | None = None) -> GenResult:
    """Render and write the artifact set; update the manifest. Idempotent."""
    artifacts = artifacts if artifacts is not None else build_artifacts(ctx)
    manifest = _load_manifest(ctx.framework_root)
    files: dict = manifest.setdefault("files", {})
    result = GenResult()

    for art in artifacts:
        target_path = ctx.project_root / art.target
        entry = files.get(art.target)

        if art.policy is MergePolicy.IN_FILE:
            rendered = art.render(ctx)  # render already accounts for existing file
        else:
            # Adoption guard: refuse to clobber a pre-existing un-managed file.
            if target_path.exists() and entry is None:
                result.refused.append((art.target, art.adopt_hint or
                    f"{art.target} already exists and is not repokit-managed; "
                    f"move it aside before generating."))
                continue
            rendered = art.render(ctx)

        new_hash = _content_hash(ctx.framework_version, rendered)
        if not _needs_write(target_path, rendered, new_hash, entry, ctx.framework_version):
            result.skipped.append(art.target)
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered, encoding="utf-8")
        files[art.target] = {
            "sources": art.sources,
            "hash": new_hash,
            "framework_version": ctx.framework_version,
        }
        result.written.append(art.target)

    manifest["framework_version"] = ctx.framework_version
    _save_manifest(ctx.framework_root, manifest)
    return result


# ── Artifact: .mcp.json (survivors only; in-file merge) ───────────────

# The driver-era servers (coderabbit, dispatch, tickets) die (ADR-1,
# demolition-sequence.md). Only the repo-tooling-awareness servers survive.
_REPOKIT_MCP_NAMES = ("lint", "repo_cmd")


def _mcp_servers(ctx: GenContext) -> dict:
    """The repokit-generated MCP server entries (the survivors)."""
    agent_cfg = ctx.config.get("agent", {})
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}

    lint_args = ["-m", "repo_tools.agent.mcp.lint"]
    if agent_cfg.get("ruff_select"):
        lint_args += ["--select", str(agent_cfg["ruff_select"])]
    if agent_cfg.get("ruff_ignore"):
        lint_args += ["--ignore", str(agent_cfg["ruff_ignore"])]

    servers = {
        "lint": {"type": "stdio", "command": ctx.python_exe, "args": lint_args},
    }

    from .repo_cmd import _discover_registered_tools
    registered = _discover_registered_tools()
    if registered:
        repo_cmd_args = [
            "-m", "repo_tools.agent.mcp.repo_cmd",
            "--project-root", ctx.project_root.as_posix(),
            "--config", "{}",
            "--extra-tools", json.dumps(registered),
        ]
        tool_dirs = get_project_tool_dirs()
        if tool_dirs:
            repo_cmd_args += ["--project-tool-dirs", json.dumps(tool_dirs)]
        servers["repo_cmd"] = {
            "type": "stdio", "command": ctx.python_exe, "args": repo_cmd_args,
        }
    return servers


def _render_mcp_json(ctx: GenContext) -> str:
    """In-file merge (GEN-5): preserve foreign servers; repokit names win."""
    existing: dict = {}
    target = ctx.project_root / ".mcp.json"
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
    merged_servers = dict(existing.get("mcpServers", {})) if isinstance(existing, dict) else {}
    merged_servers.update(_mcp_servers(ctx))  # repokit-generated names win

    out = dict(existing) if isinstance(existing, dict) else {}
    out["mcpServers"] = merged_servers
    return json.dumps(out, indent=2, sort_keys=True) + "\n"


# ── Artifact: .claude/settings.json (denies + ADR hook) ───────────────


def _render_settings_json(ctx: GenContext) -> str:
    """Gated-doc denies (best-effort) + the ADR-immutability PreToolUse hook.

    Permissions must live in project settings, not the plugin — plugins cannot
    contribute permissions (ADR-3). The real integrity guarantee is executed
    criteria + human sign-off (ADR-2); these denies are best-effort prevention.
    """
    adr_hook_cmd = " ".join([
        ctx.python_exe, "-m", "repo_tools.agent.hooks", "adr_immutable",
    ])
    settings = {
        "permissions": {
            "deny": [
                "Edit(docs/adr/**)",
                "Write(docs/adr/**)",
                "Edit(_managed/**)",
                "Write(_managed/**)",
            ],
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [{"type": "command", "command": adr_hook_cmd}],
                },
            ],
        },
    }
    return json.dumps(settings, indent=2, sort_keys=True) + "\n"


# ── Plugin scaffold (manifest + bundled skills) ──────────────────────

# The generated plugin (personas, bundled skills) is repo-level gitignored
# output. It is emitted under PLUGIN_ROOT in the project; the runner (Workflow)
# ships *beside* it under .claude/workflows/ (plugins can't bundle workflows).
#
# PLUGIN_ROOT sits under `.claude/skills/` so Claude Code auto-discovers it as a
# skills-directory plugin (`repokit@skills-dir`) — no marketplace entry or manual
# `/plugin install` (one-time workspace-trust accept aside). Its agents are then
# referenced by the namespaced form `repokit:spec-gate` / `repokit:impl-gate`.
PLUGIN_ROOT = ".claude/skills/repokit"
PLUGIN_NAMESPACE = "repokit"

# Framework-bundled skills copied verbatim into the plugin.
_BUNDLED_SKILLS = ("spike",)


def _skills_dir() -> Path:
    return Path(__file__).resolve().parent / "skills"


def _workflows_dir() -> Path:
    return Path(__file__).resolve().parent / "workflows"


def _render_plugin_manifest(ctx: GenContext) -> str:
    manifest = {"name": "repokit", "version": ctx.framework_version}
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def _bundled_file_render(src: Path) -> Callable[[GenContext], str]:
    """A render that copies a framework-bundled file verbatim."""
    def _render(_ctx: GenContext) -> str:
        return src.read_text(encoding="utf-8")
    return _render


# ── Reviewer personas (canonical → per-tool) ─────────────────────────

# Canonical, tool-neutral persona definitions. Rendered to Claude `.md`
# subagents now; a Codex `.codex/agents/*.toml` renderer can be added later
# from the SAME descriptors without touching the engine (GEN-4). The platform
# caveat (review-gates.md) holds: plugin-bundled subagents honor `tools` but
# ignore mcpServers/permissionMode/hooks — these profiles need only read-only
# tools (+ Bash for the impl gate), so nothing is lost.


@dataclass
class Persona:
    name: str
    description: str
    tools: list[str]
    prompt_file: str           # bundled under personas/
    model: str | None = None


CANONICAL_PERSONAS = [
    Persona(
        name="spec-gate",
        description=(
            "Adversarial spec-gate reviewer: verifies a work item's spec is "
            "feasible, fully evidenced (concluded spikes / cited code), free of "
            "open questions and unauthorized scope cuts, with frozen executable "
            "criteria. Read-only; defaults to fail when uncertain."
        ),
        tools=["Read", "Grep", "Glob"],
        prompt_file="spec-gate.prompt.md",
    ),
    Persona(
        name="impl-gate",
        description=(
            "Adversarial impl-gate reviewer: re-runs the spec's frozen criteria "
            "itself and hunts error-hiding, fake/cheating implementations, "
            "defensive fallbacks, spec non-conformance, criteria-gaming, and "
            "messy code. Read-only + Bash; defaults to fail when uncertain."
        ),
        tools=["Read", "Grep", "Glob", "Bash"],
        prompt_file="impl-gate.prompt.md",
    ),
]


def _personas_dir() -> Path:
    return Path(__file__).resolve().parent / "personas"


def _render_persona_claude(persona: Persona) -> Callable[[GenContext], str]:
    """Render a canonical persona to a native Claude subagent `.md`."""
    def _render(_ctx: GenContext) -> str:
        body = (_personas_dir() / persona.prompt_file).read_text(encoding="utf-8")
        fm = [
            "---",
            f"name: {persona.name}",
            f"description: {persona.description}",
            f"tools: {', '.join(persona.tools)}",
        ]
        if persona.model:
            fm.append(f"model: {persona.model}")
        fm.append("---")
        return "\n".join(fm) + "\n\n" + body.rstrip("\n") + "\n"
    return _render


# ── Artifact set ─────────────────────────────────────────────────────


def build_artifacts(ctx: GenContext) -> list[Artifact]:
    """The Claude (now) target set. Codex (deferred) adds its own here (GEN-4)."""
    artifacts = [
        Artifact(
            target=f"{PLUGIN_ROOT}/.claude-plugin/plugin.json",
            sources=["framework:plugin-manifest"],
            render=_render_plugin_manifest,
            policy=MergePolicy.OWNED,
        ),
    ]
    for skill in _BUNDLED_SKILLS:
        src = _skills_dir() / skill / "SKILL.md"
        artifacts.append(Artifact(
            target=f"{PLUGIN_ROOT}/skills/{skill}/SKILL.md",
            sources=[f"framework:skills/{skill}"],
            render=_bundled_file_render(src),
            policy=MergePolicy.OWNED,
        ))
    for persona in CANONICAL_PERSONAS:
        artifacts.append(Artifact(
            target=f"{PLUGIN_ROOT}/agents/{persona.name}.md",
            sources=[f"framework:personas/{persona.name}"],
            render=_render_persona_claude(persona),
            policy=MergePolicy.OWNED,
        ))
    # The runner (Workflow) ships beside the plugin at project-level
    # .claude/workflows/ — auto-discovered, invoked as /repokit-work-item.
    artifacts.append(Artifact(
        target=".claude/workflows/repokit-work-item.js",
        sources=["framework:workflows/work-item"],
        render=_bundled_file_render(_workflows_dir() / "work-item.workflow.js"),
        policy=MergePolicy.OWNED,
    ))
    artifacts += [
        Artifact(
            target=".mcp.json",
            sources=["config.yaml:agent", "framework:mcp-survivors"],
            render=_render_mcp_json,
            policy=MergePolicy.IN_FILE,
        ),
        Artifact(
            target=".claude/settings.json",
            sources=["framework:gated-doc-denies", "framework:adr-immutable-hook"],
            render=_render_settings_json,
            policy=MergePolicy.OWNED,
            adopt_hint=(
                ".claude/settings.json already exists and is not repokit-managed. "
                "repokit owns this file (gated-doc denies + the ADR hook); put your "
                "own settings in .claude/settings.local.json (Claude Code merges them) "
                "and remove or move aside the existing .claude/settings.json."
            ),
        ),
    ]
    return artifacts


def gitignore_entries() -> list[str]:
    """Generated build-output paths to gitignore.

    Excludes ``.mcp.json`` deliberately — it is the in-file-merge exception
    (GEN-5): a project may commit its own ``.mcp.json`` with foreign servers,
    and repokit merges into it rather than owning it. The plugin dir, the
    runner, and the repokit-owned ``settings.json`` are pure build output.
    """
    return [
        f"{PLUGIN_ROOT}/",
        ".claude/workflows/repokit-work-item.js",
        ".claude/settings.json",
    ]


def make_context(project_root: Path, framework_root: Path, config: dict) -> GenContext:
    """Build a :class:`GenContext` for a `./repo`-invoked generation."""
    return GenContext(
        project_root=project_root,
        framework_root=framework_root,
        framework_version=framework_version(framework_root),
        config=config,
        python_exe=posix_path(sys.executable),
    )
