# Spec — Demolition Sequence (scrap the driver-era machinery)

Status: draft (pending spec-review). Work item: `demolition-sequence`. Implements ADR-1.

The execution plan + file-level kill-list for the scrap work items (`scrap-dispatch-headless`,
`roles-to-subagents`, `allowlist-collapse`). Consolidated from an earlier planning draft.

## Dies vs. survives

| Dies (driver-era machinery) | Files |
|---|---|
| Subprocess dispatch tool | `repo_tools/agent/dispatch.py`, `repo_tools/agent/mcp/dispatch.py` |
| Headless mode | `--role/--ticket` subprocess runs in `tool.py`, `_process_agent_output`, `claude/_shared.py` `OUTPUT_SCHEMAS`, `--json-schema` validation |
| CLI-subprocess backend | `repo_tools/agent/claude/` (SDK backend already removed; CLI-subprocess backend joins it) |
| `roles` as an identity axis | per-role plugin dirs, `_ROLE_ALLOWED_TOOLS`, role-scoped MCP wiring, `prompts/{orchestrator,worker,reviewer}.txt` |
| Most of the allowlist | `rules.py` (~381 LOC AST parsing) + `allowlist_default.toml` manifest + `hooks/check_bash.py` + `hooks/approve_mcp.py` + `roles=` filter |
| Ticket FSM / ledger | `agent/tickets.py`, `agent/mcp/tickets.py`, `hooks/approve_ticket.py` (no progress server; phase is derived) |
| CodeRabbit integration | `agent/coderabbit.py`, `agent/mcp/coderabbit.py` (dead code — never wired in; CI review is the cloud CodeRabbit, independent of this repo) |
| Interactive-dispatch plumbing | `agent/events.py` (event subscription for headless/interactive runs), `agent/worktree.py` (the native Workflow `isolation:'worktree'` replaces it) |

| Survives (the durable core) | Where |
|---|---|
| Repo-tooling awareness | `repo_cmd`, `lint` (MCP and/or generated `agents.md`) — *the moat* |
| Spec-driven anti-cheating workflow | AI gates + executed criteria (re-run independently) + human sign-off + git (no server) |
| Native subagent personas | bundled in the generated plugin's `agents/` dir |
| Native permission config | allowlist → **zero custom code**: `permissions.deny` path rules + auto-mode classifier (ADR-3) |
| Centralized generation layer | the new mission of `init`/sync |

## Roles, split three ways

1. **Persona + toolset + model → native subagent profiles.** Delete role prompts + per-role
   plugin machinery; ship personas as native `.claude/agents/*.md` (+ Codex `.codex/agents/*.toml`)
   bundled in the generated plugin.
2. **Authority to advance the FSM → re-key identity → evidence.** `_ROLE_ALLOWED_TRANSITIONS`
   → `_PHASE_GATES`: a transition requires executed criteria + a human sign-off,
   checked in code, not the caller's role. (See `workflow.md`, ADR-2.)
3. **Who-spawns-whom (dispatch) → one orchestrator owns all fan-out.** Native subagents *can* nest
   (since CLI v2.1.172, with a depth cap on background agents), but we don't rely on it: a single
   orchestrator drives the work item and spawns worker subagents — a deliberate choice, not a platform
   limit.

## Migration order (safe demolition)

The orchestrator session holds FSM authority, so don't cut first and leave a window where
nothing can drive a work item.

1. Stand up the new path **beside** the old: native-subagent spawning + the phase-gated FSM
   (phase derived from the branch), the generated playbook/Workflow, the AI reviewer personas, and the ADR-immutability hook.
2. Verify an orchestrator can drive a work item spec→impl→review using native Task spawning + the
   phase gates, with the generated plugin loaded.
3. **Then** delete `dispatch.py`, `mcp/dispatch.py`, headless mode, `claude/` backend, role
   constants/prompts; collapse the allowlist.
4. Flip `init` to the generation mission; add the adoption guard.

Per the versioning convention, each shipped step needs a `pyproject.toml` bump + `CHANGELOG`
entry; consolidate a batch into a single final commit.

## Acceptance criteria

- [ ] **DEM-1** New path verified end-to-end before any deletion (step 2 gate).
- [ ] **DEM-2** All "Dies" files removed; no dangling imports/refs (incl. sweeping stale `__pycache__`).
- [ ] **DEM-3** `agent/tickets.py` + `agent/mcp/tickets.py` deleted (no progress server/ledger; phase derived).
- [ ] **DEM-4** Allowlist removed; permissions native (`permissions.deny` + classifier); `roles=` filter gone.
