// repokit work-item runner — the spec-driven FSM realized as a native Claude
// Workflow (ADR-2, docs/spec/workflow.md). GENERATED FILE: edits are overwritten
// on the next `./repo` generation. Amend the source under the framework.
//
// The runner owns the two GATED transitions of the FSM:
//
//     spec ──(spec-gate pass)──▶ impl ──(impl-gate pass + criteria)──▶ review
//
// `notes → spec` (create branch + author the spec, gathering concluded spikes)
// is ungated and is done INTERACTIVELY before this workflow is invoked — it
// needs human/orchestrator judgment and the `spike` skill. `review → done`
// (human sign-off + merge) is the trust boundary and also happens in-session:
// a Workflow runs autonomously and cannot prompt the human mid-run, so this
// runner returns a "ready for review" packet and the interactive session
// carries out sign-off + merge.
//
// Precondition: the orchestrator has checked out `work/<id>` with the spec
// committed on it. Invoke as `/repokit-work-item` (or via the Workflow tool),
// passing `args = { id, spec }`.

export const meta = {
  name: 'repokit-work-item',
  description:
    'Drive a repokit work item through the gated FSM (spec-gate → impl → impl-gate) ' +
    'and return a ready-for-review packet for human sign-off. Spec authoring and the ' +
    'final merge happen interactively, around this autonomous span.',
  phases: [
    { title: 'Spec gate', detail: 'adversarial spec review (repokit:spec-gate)' },
    { title: 'Impl', detail: 'implement per the approved spec' },
    { title: 'Impl gate', detail: 'adversarial impl review + criteria re-run (repokit:impl-gate)' },
  ],
}

// Structured verdict contracts — mirror the persona output profiles
// (docs/spec/review-gates.md). Forcing the schema makes the reviewer return
// validated data, not prose, and lets the gate be plain control flow.
const SPEC_VERDICT = {
  type: 'object',
  required: ['verdict', 'findings', 'checked'],
  properties: {
    verdict: { enum: ['pass', 'fail'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['type', 'location', 'evidence', 'severity'],
        properties: {
          type: { type: 'string' },
          location: { type: 'string' },
          evidence: { type: 'string' },
          severity: { enum: ['low', 'medium', 'high'] },
        },
      },
    },
    checked: { type: 'array', items: { type: 'string' } },
  },
}

const IMPL_VERDICT = {
  type: 'object',
  required: ['verdict', 'findings', 'criteria_results', 'checked'],
  properties: {
    verdict: { enum: ['pass', 'fail'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['type', 'file', 'evidence', 'severity'],
        properties: {
          type: { type: 'string' },
          file: { type: 'string' },
          evidence: { type: 'string' },
          severity: { enum: ['low', 'medium', 'high'] },
        },
      },
    },
    criteria_results: {
      type: 'array',
      items: {
        type: 'object',
        required: ['command', 'passed'],
        properties: {
          spec_item: { type: 'string' },
          command: { type: 'string' },
          exit_code: { type: 'number' },
          passed: { type: 'boolean' },
        },
      },
    },
    checked: { type: 'array', items: { type: 'string' } },
  },
}

const item = args || {}
const id = item.id || 'work-item'
const specPath = item.spec || `docs/spec/${id}.md`
const MAX_IMPL_ATTEMPTS = 3

// ── Spec gate — spec → impl (advance only on pass) ───────────────────
phase('Spec gate')
const specVerdict = await agent(
  `You are the spec gate for repokit work item '${id}'. Review the spec at ` +
  `\`${specPath}\` and every ADR it depends on. Verify: feasibility, every ` +
  `non-trivial claim cites a CONCLUDED spike / code path / doc, no open ` +
  `questions, no unauthorized scope cuts (check stated goals + non-goals), and ` +
  `every spec item has evidence + a (preferably executable) criterion. Default ` +
  `to fail when uncertain. Return your structured verdict.`,
  { agentType: 'repokit:spec-gate', label: `spec-gate:${id}`, phase: 'Spec gate', schema: SPEC_VERDICT }
)
if (!specVerdict || specVerdict.verdict !== 'pass') {
  // Back-edge: spec is wrong. The fix is an interactive spec edit + re-gate,
  // not an autonomous rewrite of a judgment-laden, sensitive doc.
  return {
    status: 'spec-rejected',
    id,
    spec_verdict: specVerdict,
    message:
      `Spec gate REJECTED '${id}'. Address the findings by editing ${specPath} ` +
      `(and/or gathering concluded spikes), then re-run the workflow.`,
  }
}

// ── Impl + impl gate — impl → review (bounded back-edge on fail) ──────
let implVerdict = null
let attempt = 0
while (attempt < MAX_IMPL_ATTEMPTS) {
  attempt++
  const prior = implVerdict
    ? `Prior impl-gate findings you MUST fix:\n${JSON.stringify(implVerdict.findings, null, 2)}`
    : 'This is the first attempt.'
  phase('Impl')
  await agent(
    `Implement repokit work item '${id}' on the current branch per the APPROVED ` +
    `spec \`${specPath}\`. Do exactly what the spec says — no scope cuts, no ` +
    `error-hiding, no fake/stub implementations, no defensive fallbacks. Cite ` +
    `spec-item IDs in the code (e.g. \`# implements DEM-4\`). Make the spec's ` +
    `frozen criteria genuinely pass. ${prior}`,
    { label: `impl:${id}#${attempt}`, phase: 'Impl' }
  )
  phase('Impl gate')
  implVerdict = await agent(
    `You are the impl gate for work item '${id}'. Adversarially review the ` +
    `implementation on this branch against the approved spec \`${specPath}\`. ` +
    `RUN the spec's frozen criteria yourself (via the repo tooling) and record ` +
    `their exit codes — do not trust any claim that they pass. Hunt for ` +
    `error-hiding, cheating/fake impl, defensive fallbacks, spec ` +
    `non-conformance, criteria-gaming, and messy code. Default to fail when ` +
    `uncertain. Return your structured verdict.`,
    { agentType: 'repokit:impl-gate', label: `impl-gate:${id}#${attempt}`, phase: 'Impl gate', schema: IMPL_VERDICT }
  )
  if (implVerdict && implVerdict.verdict === 'pass') break
}

if (!implVerdict || implVerdict.verdict !== 'pass') {
  return {
    status: 'impl-blocked',
    id,
    attempts: attempt,
    impl_verdict: implVerdict,
    message:
      `Impl gate did not pass '${id}' after ${attempt} attempt(s). Review the ` +
      `findings + criteria results; the work item needs human attention.`,
  }
}

// ── Ready for review — the human gate is a workflow boundary ─────────
return {
  status: 'ready-for-review',
  id,
  attempts: attempt,
  spec_verdict: specVerdict,
  impl_verdict: implVerdict,
  message:
    `Work item '${id}' passed the spec gate and the impl gate ` +
    `(${attempt} impl attempt(s)). Present the git diff + both verdicts + the ` +
    `criteria results to the human for sign-off. On approval, merge ` +
    `work/${id} → main with an \`Approved-by:\` trailer ("done" = merged).`,
}
