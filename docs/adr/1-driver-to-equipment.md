# ADR-1 — Repokit is equipment Claude loads, not a driver of Claude

Status: proposed (pending human review)
Date: 2026-06

## Context

Repokit was built when the Claude CLI lacked native orchestration, so it reimplemented
dispatch and headless execution: it shelled out to run `claude` as a subprocess per role
(`dispatch.py`, `mcp/dispatch.py`, headless `--role/--ticket` runs, the `claude/` backend,
structured `OUTPUT_SCHEMAS`). The CLI has since matured: native subagents, per-agent
profiles/models/tools, permission modes, and a plugin/marketplace system.

## Decision

Invert the relationship. Repokit stops *driving* Claude and becomes *equipment Claude loads*:
a plugin providing MCP servers (repo-tooling awareness), bundled native subagent personas, and
hooks, plus the spec-driven FSM realized as a native Workflow generated to project-level
`.claude/workflows/` (plugins can't bundle workflows, so it ships beside the plugin). Orchestration
is delegated to the native runtime; a single orchestrator session owns all fan-out and spawns worker
subagents via the native Task tool.

Consequently, delete: `dispatch.py`, `mcp/dispatch.py`, headless mode, the `claude/`
subprocess backend, and `roles` as an identity axis (persona → native subagents; FSM
authority → evidence/phase gates; dispatch → single orchestrator).

## Consequences

- Large net deletion; the durable core (tooling MCP, FSM, personas, generation layer)
  remains. Permissions are fully native — no custom permission code (ADR-3).
- A single orchestrator drives fan-out across work items (a *choice* — native subagents can nest, but
  we don't rely on it).
- Distribution and init simplify (config generated into the repo; init becomes
  config-surface generation). See `generation.md`.

## Supersedes / superseded by

None. Amendments must be made via a superseding ADR, never by editing this file.
