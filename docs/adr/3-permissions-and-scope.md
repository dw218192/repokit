# ADR-3 — Permissions are native and user-owned; outward safety is out of scope

Status: proposed (pending human review)
Date: 2026-06

## Context

The allowlist (`rules.py` ~381 LOC of bashlex/pygments command parsing, plus the
`allowlist_default.toml` manifest and the `check_bash`/`approve_mcp` hooks — ~615 LOC of surface
in total) was built when native permission settings were **unreliable and unclear**, and when
there was **no auto mode** — so an unattended run had no safe-but-autonomous option (it was
ask-everything or YOLO). Both have since changed: auto mode now exists — a server-side classifier
plus a user-configurable trusted-environment surface — and `permissions.deny` rules are clear and
declarative. So the allowlist — and even the planned thin deny hook — is unnecessary.

## Decision

Ship **zero custom permission code.**

- **Outward-action safety is an explicit non-goal, owned by the user.** Force-push, network
  exfiltration, and `rm -rf` outside the tree are gated only by the platform's native auto-mode
  classifier, which the *user* configures — they run the coding CLI directly and own its
  permission posture. repokit emits no permission code and accepts this residual risk by design;
  sandboxing the agent's hands is not repokit's job.
- **Integrity is a separate stack (ADR-2) and does not rely on permission denies.** The
  gated-doc `permissions.deny` rules (deny `Edit`/`Write` on `docs/adr/**` and `_managed/**`)
  are best-effort prevention only, and are emitted into the
  project's `.claude/settings.json` — **not** the plugin, since plugins cannot contribute
  permissions. The real integrity guarantee is executed criteria + a human sign-off (ADR-2) —
  never the deny.

| | Owner | Mechanism | Guarantee |
|---|---|---|---|
| Permissions (outward safety) | user | native classifier / CLI config | best-effort, accepted risk |
| Integrity (anti-cheating) | repokit | executed criteria + human sign-off | mechanical + trust |

## Consequences

- `rules.py`, the allowlist manifest, the `roles=` filter, and the planned deny hook are all
  deleted; `allowlist-collapse` is a *removal*, not a rewrite.
- Completes the de-machining (ADR-1): repokit *emits* permission config, it does not *run* a
  permission layer — consistent with "equipment Claude loads, not a driver."
- The gated-doc denies live in generated `.claude/settings.json` (in-repo), not the plugin.
- Residual outward-action risk is accepted and user-owned (see Decision); worktree isolation
  covers filesystem blast radius but not network/push, by design.

## Supersedes

Refines the allowlist-collapse intent in ADR-1. Amend via a superseding ADR only.
