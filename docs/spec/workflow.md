# Spec — The spec-driven workflow

Status: draft (pending spec-review). Work item: `workflow`. Implements ADR-2.

## One branch = one work item

No "units" registry. A work item is one branch (`work/<id>`) with one spec. The roadmap lists work
items; each runs `note → spec → impl → review → done`. The orchestrator may have several branches in
flight; nothing aggregates them into a ledger.

## Phases & transitions

| from | to | gate |
|---|---|---|
| notes | spec | — (create branch + spec) |
| spec | impl | spec-gate pass (spec fully signed) + depended-on ADRs accepted |
| impl | review | impl-gate pass + criteria pass |
| impl | spec | back-edge: spec wrong → edit + re-gate |
| review | done | human sign-off → merge `work/<id>`→`main` |
| review | impl | back-edge: issues found |

`done` is terminal (merged). The intermediate spec/impl gates are agent-run AI reviews (variance
reduction — `review-gates.md`); the only mechanically-checked transition is `→done` (criteria + human
sign-off).

## State: derived, not stored

The framework persists nothing. **Durable** = git (the spec/ADR docs + the merge = acceptance).
**Phase** = *derived* by re-running the gates against the branch (furthest passing gate); re-deriving
is self-verifying. **Review verdicts + criteria results** = ephemeral (the agent's session scratch).
No server, no ledger, no `progress.json`.

## The runner (the process, realized per tool — interactive)

The FSM above is realized as a **runner**, not an MCP or a DSL, and it runs **in the interactive
session** — the wrapped CLIs are used interactively; repokit never drives them headless (ADR-1).

- **Claude (now):** the runner *is* a native **Claude Workflow** (JavaScript — Claude's own
  workflow-authoring format), invoked in-session, generated into the project's `.claude/workflows/`
  (repo-level, gitignored build output — plugins can't bundle workflows, so the runner ships beside the
  plugin, not inside it). Its control flow *is* the FSM; gates are control flow (advance only on pass) —
  deterministic.
- **Codex (deferred):** Codex (a Rust CLI) has no native workflow primitive and is used interactively,
  so its realization is the playbook expressed as **`agents.md` instructions the agent follows + hooks**
  (`PreToolUse` deny / `Stop` block) that *enforce* the gates in-session. Weaker on sequencing than a
  script, but the tool-level gates (don't-edit-an-accepted-ADR, don't-stop-until-criteria-pass) are
  hook-enforced.

Door open without hardcoding Claude: the **canonical playbook** (phases, gates, what each does) is
tool-agnostic; each tool realizes it natively (Workflow vs instructions + hooks). We assume **no**
non-interactive driver and depend on **no** shared scripting runtime.

**The human gate is a workflow boundary.** A Workflow runs autonomously to completion — it cannot
pause mid-run for interactive human input. So the *AI* spans run inside the workflow (spec → impl →
impl-gate; each phase's subagent is `await`ed, so a stage can take arbitrarily long), and at the human
sign-off gate the workflow returns "ready for review" to the interactive session, where the human
approves and the merge happens. The full process = workflow span(s) **bracketed by human gates**, not
one uninterrupted run. (Our design has a human only at the final gate, so it's one AI span + the
human sign-off.)

## Criteria & sign-off

- **Criteria** are commands in the spec (e.g. `./repo test`), run via the repo tooling and
  **independently re-run by the impl-gate** (+ CI) — that re-run, not a trusted server, catches a
  lying agent.
- **Sign-off / done**: the agent presents diff + criteria results + verdicts and **asks the human**;
  on approval it merges with an `Approved-by:` trailer. Trust-based (ADR-2): an unauthorized sign-off
  is possible — accepted risk.

## ADRs

Plain markdown docs (no MCP), schema-governed frontmatter + frozen body:

```json
{ "id": "^ADR-[0-9]+$", "title": "...", "status": "proposed|accepted|superseded",
  "date": "...", "supersedes": [], "superseded_by": [] }
```

No `rejected` (a rejected ADR isn't committed). **Immutability** = git history (accepted version
immutable in history) + a portable `PreToolUse` hook denying edits to an *accepted* ADR + the runner
only superseding (new ADR), never editing.

## Permissions

Native (ADR-3): `Write` on `_managed/**` denied (build cache); the ADR-immutability `PreToolUse` hook;
spec docs stay agent-editable (reviewed via git diff at the gates). No custom permission layer.

## Acceptance criteria

- [ ] **WF-1** One branch = one work item; phases enforced with back-edges; `done` = merged, terminal.
- [ ] **WF-2** Phase is derived by re-running gates; no server/ledger/persisted state; review + criteria results ephemeral.
- [ ] **WF-3** The runner is a native Claude Workflow (JavaScript) generated to project-level `.claude/workflows/` (not plugin-bundled — plugins can't ship workflows) — no DSL; gates are control flow.
- [ ] **WF-4** The canonical playbook is tool-agnostic; a Codex realization (interactive: `agents.md` + hooks) can be added without changing it.
- [ ] **WF-5** Criteria run via repo tooling, independently re-run by the impl-gate (+ CI); agent never self-reports pass/fail.
- [ ] **WF-6** Done = sign-off-trailered merge to `main`.
- [ ] **WF-7** ADRs: plain docs, status ∈ {proposed, accepted, superseded}; immutability = git history + PreToolUse hook + supersede-only.
