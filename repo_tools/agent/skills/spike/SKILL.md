---
name: spike
description: Resolve an uncertainty during spec authoring by running a throwaway investigation and recording a concluded docs/spike/SPIKE-<n>.md evidence artifact. Use when a spec or ADR makes a design claim that is not yet backed by a concluded spike, a cited code path, or a doc — the spec gate rejects uncited claims and open questions.
---

A **spike** is a throwaway investigation that resolves an uncertainty during
spec authoring. It is an *evidence artifact*, **not a lifecycle phase** —
gathering evidence is part of completing the spec. Spikes are ungated (edit
freely), but they have a schema because **every non-trivial spec/ADR claim must
cite a concluded spike** (or existing code / a doc). An uncited claim, or a
claim citing an `open` spike, is a spec-gate finding.

## When to spike

- A spec/ADR asserts something about how a tool, library, or the codebase
  behaves that you have not verified.
- A design depends on a capability you are assuming exists.
- There is an open question the spec cannot answer without trying something.

If the claim is already backed by a code path you can cite (`file:line`) or an
authoritative doc, cite that instead — do not spike what is already evidenced.

## How to run a spike

1. **Frame the question.** State the single uncertainty in one sentence. One
   spike resolves one question.
2. **Investigate.** Read code, run a throwaway script, probe the tool, check a
   doc. Keep the throwaway artifacts (commands, snippet paths, links) for `refs`.
3. **Conclude.** Write the finding that actually resolves the question — the
   answer, not a summary of the attempt. If the investigation is inconclusive,
   the spike stays `open` and is **not** usable as evidence yet; keep going.
4. **Record it** as `docs/spike/SPIKE-<n>.md` with the frontmatter below, set
   `status: concluded`, and cite its id (e.g. `SPIKE-3`) from the spec/ADR claim.

Number spikes sequentially (`SPIKE-1`, `SPIKE-2`, …) — check `docs/spike/` for
the next free number.

## Frontmatter schema

```yaml
---
id: SPIKE-3                 # ^SPIKE-[0-9]+$   (required)
title: Short title          # required
question: The uncertainty being investigated.   # required
method: What was tried.                          # optional but expected
findings: The conclusion that resolves the question.   # the evidence
status: concluded           # open | concluded  (required; only concluded is evidence)
refs:                       # throwaway code / links
  - path/to/throwaway.py
  - https://example/doc
---
```

Rules enforced at the spec gate:

- A spec/ADR may cite **only `concluded`** spikes; an `open` spike is not evidence.
- **No open questions in an approved spec** — unresolved uncertainty must be
  spiked and concluded first.
- Every non-trivial design claim cites evidence (a concluded spike, a code path,
  or a doc). An uncited claim is a finding.

## Body

Below the frontmatter, write the investigation narrative: what you tried, what
you observed, and why the finding follows. Be concrete — the spec gate reads
this to confirm the cited spike *actually supports* the claim (a spike whose
findings don't back the claim is an infeasibility finding).
