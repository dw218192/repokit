# Spec — Generation & cross-tool distribution

Status: draft (pending spec-review). Work item: `generation`. Implements ADR-1.

## Model

repokit *generates* an agent-config surface from hand-authored sources; it runs no servers. Generated
output is **gitignored build output**, regenerated on `./repo` invocation (deterministic from pinned
inputs, so committing it is redundant).

- **Sources (committed, hand-authored):** `config.yaml` (persona specs, hook config), `agents.md`,
  `docs/**`, project-specific `.claude`/`.codex` content.
- **Generated (gitignored, overwrite):** the plugin — personas, bundled skills (incl. `spike`) — plus
  the runner (Workflow) at project-level `.claude/workflows/` (plugins can't bundle workflows; it loads
  alongside the plugin), `claude.md`, the `repo*` shims, `.claude/settings.json` deny + the ADR hook.

Invariant: the merge boundary is between files, never inside one — except `.mcp.json` (in-file merge;
repokit-generated names win).

## Regeneration

On `./repo` invocation, `_managed/manifest.json` (a local build cache: per-file `sources` + `src_hash`,
including the pinned framework version) decides what to regenerate (stale / missing / hand-edited →
regenerate). No git hooks — there are no committed artifacts to keep fresh. Adoption guard: a
pre-existing non-generated `claude.md` is refused, not overwritten (migrate it into `agents.md`).

## Instructions

`agents.md` is the source of truth (cross-tool standard; Codex reads it natively). `claude.md` is
generated from it (a repo-layer view; framework instructions ship in the plugin).

## Cross-tool: canonical → per-tool targets (Claude now, Codex deferred)

One canonical source emits each runtime's native config — **no DSL**, plain per-tool emission. Codex is
**deferred**; nothing canonical hardcodes Claude.

| | Claude (now) | Codex (deferred) |
|---|---|---|
| Personas | `.claude/agents/*.md` | `.codex/agents/*.toml` |
| MCP (tooling) | `.mcp.json` | `.codex/config.toml` `[mcp_servers]` |
| Permissions | `.claude/settings.json` deny + hooks | `.codex/config.toml` + hooks |
| Instructions | `claude.md` (from `agents.md`) | `agents.md` (native) |
| Process / runner | native **Workflow** (JS, in-session) | `agents.md` instructions + hooks (interactive; deferred) |

## Acceptance criteria

- [ ] **GEN-1** Generated surface gitignored; sources committed; regeneration deterministic from pinned inputs.
- [ ] **GEN-2** Regeneration on `./repo` invocation via manifest staleness; a framework-version change triggers it; no git hooks.
- [ ] **GEN-3** Adoption guard refuses a non-generated `claude.md`.
- [ ] **GEN-4** Personas / runner / permissions emit from one canonical source to each tool's native config; Codex deferred, nothing hardcodes Claude.
- [ ] **GEN-5** `.mcp.json` in-file merge precedence defined (repokit-generated wins).
