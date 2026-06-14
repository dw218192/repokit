# ADR-2 — Workflow integrity model (anti-cheating)

Status: proposed (pending human review)
Date: 2026-06

## Context

repokit exists to keep an autonomous coding agent honest about *this* repo. Earlier drafts built
integrity on content-hash "seals" and then on a trusted "progress server" that ran criteria and owned
a ledger. Both proved unnecessary: criteria can be verified by *independent re-execution*, the durable
record is git, and the human sign-off is a trust point we deliberately accept. Integrity reduces to —
**executed criteria (re-run independently), git, an agent-driven human sign-off, and AI review
gates** — none of which require a custom server or persisted state.

## Trust boundary

- **Mechanical:** criteria are *executed* and verified by **independent re-execution** — the impl-gate
  reviewer (a fresh adversarial subagent) and CI run them, so the authoring agent's claim is never the
  check. **Git is the durable record** (commits + the merge). No server owns any state; phase is
  *derived* by re-running the gates against the branch.
- **Trust-based (deliberately small):** the **human sign-off**. The process is agent-driven, so the
  agent asks the human and records the decision — not mechanically unforgeable. Accepted residual risk.

Corollary: integrity needs almost **no custom runtime** — independent re-execution (native subagents +
CI), git, and the AI reviewer *personas* (config) carry it. The framework's job is to *generate* that
setup, not to *run* a trusted server. ("Zero permission code" — ADR-3 — now extends toward near-zero
integrity code too.)

## Decision — the mechanisms

1. **Criteria are executed, not self-reported.** A criterion carries a command. The impl-gate reviewer
   and CI run it and observe the exit code — *independent re-execution*, not the authoring agent's
   word, is the check. Criteria with no command are human-verified at sign-off. No agent-writable
   `met` flag.

2. **Acceptance is an agent-driven human sign-off.** The agent presents the diff + criteria results +
   AI verdicts and **asks the human**; on approval it merges `work/<id>`→`main` with an `Approved-by:`
   trailer ("done" = merged). The human runs no commands. Trust point, not mechanical: an
   unauthorized/fabricated sign-off is possible — **accepted by design** (you can't constrain
   everything); the executed criteria + AI gates + git keep the trust surface small. Always against
   the current state.

3. **Git is the record.** History, diffs, and the immutability of committed decisions come from git;
   repokit doesn't reinvent them. A spec found wrong, or a contract-changing refactor, is just an
   **edit + re-sign-off** — the agent edits and asks the human to re-approve against the new diff. No
   supersession ceremony; ADR "immutability" is git history + review-at-gate. No stored phase, no
   ledger — phase is derived.

## What this guarantees — and doesn't

- **Guaranteed (mechanical):** machine-checkable criteria are verified by independent re-execution
  (impl-gate + CI), not the authoring agent's claim; what shipped is recorded in git.
- **Trust-based:** the human sign-off — the agent records it, so an unauthorized sign-off is possible
  (accepted residual risk, alongside outward/OS safety, which is the user's — ADR-3).

## Inception clause

While authoring the foundational set (before the AI gates + tooling exist), docs are authored by
direct edit; the discipline (independent criteria re-runs, the gates, agent-recorded sign-off)
activates once the tooling ships.

## Consequences

- **No progress server, no ledger, no persisted state.** `agent/mcp/tickets.py` (the ticket
  FSM/ledger) is deleted, not refactored. Phase is derived; criteria run via the repo tooling.
- ADRs are plain docs (no `adr` MCP); immutability = git history + a portable `PreToolUse` hook +
  supersede-only (`workflow.md`).
- The FSM is realized as a native Claude **Workflow** (JavaScript — no DSL; a Codex realization via
  `agents.md` + hooks later), not an MCP (`workflow.md`). The process is the script; gates are control
  flow. All usage is interactive — repokit never drives the CLI headless.
- Dropped across drafts: seals / `seal_log` / `revise_spec` / canonicalization, the unforgeable
  sign-off token, and the trusted progress server — each redundant with git + independent
  re-execution + a small trusted-human sign-off.
- Single-orchestrator (ADR-1) is a *choice*; the human sign-off is the one small trust point.

## Supersedes

Consolidates and replaces the earlier separate ADRs on seals and CRUD-gating. Amend via a superseding
ADR only (post-inception).
