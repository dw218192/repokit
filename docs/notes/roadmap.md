# Roadmap — Repokit Modernization

General-purpose planning note (ungated). Detailed design lives in `docs/spec/`.

## Thesis

Repokit keeps an autonomous coding agent **honest about *this* repo** — a spec-driven
commitment device + repo-tooling awareness. It is not an orchestration engine. The Claude
CLI has matured (native subagents, profiles, permission modes, plugins), so the driver-era
orchestration machinery is obsolete.

## The inversion

Repokit stops *driving* Claude (subprocess running headless agents) and becomes *equipment
Claude loads* (a plugin: MCP + native subagent personas + hooks + the spec FSM). See
ADR-1 and `docs/spec/demolition-sequence.md`.

## Work items

| Work item | What | Phase |
|---|---|---|
| `workflow` | Spec-driven workflow: lifecycle, runner (native Workflow, no DSL), criteria/sign-off, ADRs | spec |
| `generation` | Generation + cross-tool distribution (Claude now, Codex deferred) | spec |
| `review-gates` | AI spec/impl reviewer profiles + evidence (spikes) + spec-item IDs | spec |
| `demolition-sequence` | File-level kill-list + safe migration order for the scrap work items | spec |
| `roles-to-subagents` | Split roles three ways; personas → native subagents | notes |
| `scrap-dispatch-headless` | Remove `dispatch.py`, headless mode, `claude/` backend | notes |
| `allowlist-collapse` | ~615 LOC of allowlist surface (rules.py + manifest + hooks) → 0 custom code (native `permissions.deny` + classifier) | notes |
| `init-mission-shift` | `init` from venv-bootstrap → config-surface generation | notes |

## Decomposition

Planning (brainstorm/roadmap) decomposes the initiative into **work items**; each runs the core loop
`note → spec/adr → impl → review`. **The agent decides how to implement a work item** — the framework
does not prescribe sub-steps. A single orchestrator owns fan-out across work items; each is one branch
that merges to `main` at `done`. No units registry — the roadmap is the list, each spec carries its own
status.

## Open questions

None — all resolved. Cross-tool decided: **Claude + Codex** from one canonical source
(`generation`). Review gates are per-work-item (`review-gates`). The step sub-tier was dropped — the
agent decides impl; the core loop is per-work-item (`workflow`).
