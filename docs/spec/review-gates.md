# Spec — Review Gates & Reviewer Profiles

Status: draft (pending spec-review). Work item: `review-gates`. Implements ADR-2.

## Thesis: variance reduction

No one hand-writes code anymore; agents do. Agents are high-variance — they cut corners, cheat,
deceive, and write messy code unless constrained. Every gate here exists to clamp a specific
source of that variance. Three gates, in order:

1. **Spec gate (AI, adversarial)** — prevents infeasible / unevidenced / under-scoped designs from
   ever being executed.
2. **Impl gate (AI, adversarial)** — prevents bad coding practice, error hiding, and cheating.
3. **Human gate (trust)** — the final agent-recorded human sign-off (ADR-2).

**The AI gates are high-recall variance reducers; the human gate is the trust boundary.** An AI
reviewer can also be lazy or gamed, so it never *is* the gate — it makes the human's job tractable
by catching the bulk cheaply. Hence every AI reviewer must itself cite evidence (a `pass` verdict
enumerates what was checked); a reviewer that rubber-stamps is just a new variance source.

## Evidence layer: spikes

A **spike** is a throwaway investigation that resolves an uncertainty during spec authoring — an
evidence artifact, **not a lifecycle phase** (gathering evidence is part of completing the spec).
Spikes are ungated (free editing) but have a schema because **spec/ADR claims must cite a concluded
spike** (or existing code / doc) as evidence.

Spiking is a **bundled skill** shipped with repokit (in the generated plugin): the agent invokes it to
investigate the question and produce a concluded `docs/spike/SPIKE-<n>.md`. It is framework-provided,
like the reviewer personas — not something each repo re-authors.

```json
{
  "$id": "spike.schema.json",
  "comment": "frontmatter for docs/spike/*.md; ungated, validated at the spec gate",
  "type": "object",
  "required": ["id", "title", "question", "status"],
  "properties": {
    "id":       { "type": "string", "pattern": "^SPIKE-[0-9]+$" },
    "title":    { "type": "string" },
    "question": { "type": "string", "description": "the uncertainty being investigated" },
    "method":   { "type": "string", "description": "what was tried" },
    "findings": { "type": "string", "description": "the conclusion that resolves the question" },
    "status":   { "enum": ["open", "concluded"] },
    "refs":     { "type": "array", "items": { "type": "string" }, "description": "throwaway code / links" }
  }
}
```

Rules (enforced at the spec gate):
- A spec/ADR may cite only **`concluded`** spikes; an `open` spike is not evidence.
- Every non-trivial design claim cites evidence (spike, code path, or doc). Uncited claim = finding.
- **No open questions in an approved spec.** Unresolved uncertainty must be spiked and concluded
  first.
- **Acceptance criteria are authored and frozen at the spec gate**, not at impl — so impl is judged
  against pre-committed (preferably executable) checks, never criteria retrofitted to the code.

### Spec items have IDs

Each spec assigns its normative items stable IDs — a short per-spec prefix + number, e.g. `GS-3`
(generation), `WF-2`, `RG-5`. IDs make every requirement:

1. **citable in code** — `# implements GS-3` in the impl, checked by the impl gate;
2. **validatable** — a spike, ADR, or criterion ties to a specific item, so evidence and
   verification are addressable, not vague;
3. **traceable** — a criterion (in the spec) carries `spec_item: "GS-3"`, giving a traceability
   matrix: spec item → evidence → criterion (command) → code cite.

A spec is **fully signed** (complete) only when every item has evidence + a criterion + spec-gate
sign-off. The spec gate fails a spec with any item missing one of the three.

## Spec gate — reviewer profile

Adversarial stance: assume the spec is infeasible, over-claimed, or hiding cuts until proven
otherwise. Default to **fail** when uncertain.

Hunts for:
1. **Unauthorized deferrals / scope cuts** — requirements from the unit's goal silently dropped,
   weakened, or pushed to "later" without authorization. (Requires the spec to state explicit
   goals + non-goals so cuts are detectable.)
2. **Claims without evidence** — design assertions not backed by a concluded spike, a cited code
   path, or a doc.
3. **Open questions / vague design** — TBD / "figure out later" / ambiguous or non-executable
   criteria where executable ones are possible.
4. **Infeasibility** — the cited spike does not actually support the claim, or the design
   contradicts a known constraint.

- Tools: read-only (Read, Grep, Glob). No write.
- Output (structured): `{ verdict: pass|fail, findings: [{ type, location, evidence, severity }],
  checked: [...] }`. Every finding cites a spec location; a `pass` enumerates `checked`.
- Pass condition: zero open-question/uncited-claim findings; deferrals all authorized; criteria
  present and (where possible) executable.

## Impl gate — reviewer profile

Adversarial stance: assume the code cheats, hides errors, or cuts corners until proven otherwise.
Runs the frozen criteria itself rather than trusting the agent's claim. Default to **fail** when
uncertain.

Hunts for (rubric = `coding-standards.md`):
1. **Error hiding** — bare `except`, broad catch-and-swallow, silently swallowed failures.
2. **Cheating / fake implementation** — fallback-with-hardcoding (`try: real() except: return
   <hardcoded>`), stubbed returns that make criteria pass without real behavior, tests asserting
   trivialities.
3. **Defensive fallbacks** — try/except around imports, `getattr` defaults for required attrs,
   None-guards on non-optional values.
4. **Spec non-conformance** — impl does not do what the approved spec says, or games the criteria
   instead of satisfying intent.
5. **Criteria-gaming** — a criterion's test was weakened/skipped to pass; tests don't cover
   meaningful behavior.
6. **Messy code** — unnecessary abstractions for one-off operations, non-DRY, dead code.

- Tools: read-only + Bash to run the spec's criteria (via the repo tooling) — to verify, not trust.
- Output (structured): `{ verdict: pass|fail, findings: [{ type, file, line, evidence, severity }],
  criteria_results: [...], checked: [...] }`. Every finding cites `file:line`.
- Pass condition: all machine criteria pass under the reviewer's own run; zero
  cheating/error-hiding findings.

## Human gate

Agent-driven sign-off (ADR-2): the agent presents the git diff + the two AI verdicts (which
front-loaded the tedious hunting) and **asks the human to sign off**; it records the decision. The
human does not run commands or edit files. This is the trust boundary — and a *trust* point, not a
mechanical one: an unauthorized/fabricated sign-off is possible, an accepted residual risk. The AI
gates exist to keep the human's review tractable and that trust surface small.

## Review is ephemeral (not recorded)

AI review verdicts are **session-scoped**: a reviewer runs, its verdict gates one transition, and the
findings are then discarded — they live only in the agent's own session scratch/bookkeeping, never
framework state. Nothing is persisted at all — not even the phase: on resume it is re-derived by
re-running the gates against the branch (`workflow.md`). Re-entering a phase (a back-edge) re-runs
the gate fresh.

- `spec→impl` requires a spec-gate **pass this session**.
- `impl→review` requires an impl-gate **pass this session** + machine criteria pass.
- `review→done` requires the human sign-off recorded on the merge (ADR-2).

Gates are per-work-item; how the agent structures impl is its own concern, not separately gated.

## Reviewer personas

Both profiles are canonical-defined in `config.yaml` and compiled to native `.claude/agents/*.md`
(+ Codex `.codex/agents/*.toml`) **bundled in the generated plugin** (ADR-1) — both runtimes
support plugin-bundled subagents. Each runs in a fresh subagent context (it did not write what it
reviews), spawned by the orchestrator at the gate.

Platform caveat: plugin-bundled subagents ignore per-agent `mcpServers`/`permissionMode`/`hooks`
frontmatter (for security). That doesn't bite here — the reviewers need only read-only tools + Bash
(to run the spec's criteria via the repo tooling), and `tools`/`disallowedTools` still apply to
plugin-bundled subagents, so the read-only-plus-Bash profile holds without any scoped MCP.

## Acceptance criteria

- [ ] **RG-1** Spike schema defined; spec gate rejects citations of `open` spikes and any open question.
- [ ] **RG-2** Criteria are frozen at the spec gate; impl gate runs them itself.
- [ ] **RG-3** Spec-gate reviewer catches deferrals/scope-cuts, uncited claims, open/vague design.
- [ ] **RG-4** Impl-gate reviewer catches error-hiding, fallback-hardcoding, criteria-gaming, messy code.
- [ ] **RG-5** AI verdicts gate transitions in-session (not persisted); a `pass` enumerates what was checked.
- [ ] **RG-6** Human sign-off recorded in `accepted` is required for `review→done`; AI verdicts never substitute for it.
