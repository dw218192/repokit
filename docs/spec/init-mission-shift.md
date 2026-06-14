# Spec — init mission shift (config-surface generation)

Status: draft (pending spec-gate). Work item: `init-mission-shift`. Implements ADR-1.
Branch: `work/init-mission-shift`. Part of the v0.9.0 modernization (no version bump).

## Goal

`./repo init` gains the **config-surface-generation** mission: after bootstrapping
the managed venv it invokes the Phase-A generation layer to emit the agent-config
surface (the skills-dir plugin, the workflow runner, `.mcp.json`, `.claude/settings.json`)
and gitignore the build output — via a **shared helper** also used by `./repo generate`,
so the two entry points cannot drift. This completes the "init from venv-bootstrap →
config-surface generation" shift (roadmap; `demolition-sequence.md` step 4: "Flip `init`
to the generation mission; add the adoption guard").

## Non-goals

- **`claude.md`-from-`agents.md` cross-tool instruction generation** — the
  `agents.md` → `claude.md` instruction view (generation.md, "Instructions") is
  **deferred**. init keeps its existing `_generate_claude_template` "## Repo tooling"
  section-append for now; this work item does not touch it.
- **Removing the venv bootstrap** — init still bootstraps the managed venv (that is
  how the framework runs); the mission *shift* is additive, not a removal of bootstrap.
- **Per-`./repo`-invocation auto-regeneration** — GEN-2's "regenerate on every `./repo`
  invocation" is out of scope; `./repo init` and `./repo generate` are the explicit
  triggers. The manifest staleness cache already makes a redundant call cheap.

## Evidence

- The generation layer already exists and is invoked exactly this way by
  `./repo generate` today: `repo_tools/agent/generate_cmd.py` (`make_context` +
  `generate` + `patch_gitignore`) and `repo_tools/agent/generate.py`
  (`generate()`, `gitignore_entries()`, and the adoption guard inside `generate()`
  that returns `GenResult.refused` for a pre-existing un-managed owned file). Cited
  code — no spike needed.
- Mandate: `docs/notes/roadmap.md` (the `init-mission-shift` row) and
  `docs/spec/demolition-sequence.md` step 4.

## Spec items

- **IMS-1** Extract the generate-and-gitignore logic into a shared helper
  `generate_surface(workspace_root, framework_root, config) -> GenResult` in
  `repo_tools/agent/generate.py`, called by **both** `GenerateTool` and `InitTool`
  (no duplicated generate+gitignore logic across the two).
- **IMS-2** `InitTool.execute` calls `generate_surface` after `_bootstrap.run`,
  emitting the agent-config surface and gitignoring the build output. Covered by a
  new test `test_init_generates_agent_surface` asserting the surface (e.g.
  `.mcp.json`, `.claude/settings.json`) is emitted on the init path.
- **IMS-3** The adoption guard is honored on the init path: a pre-existing
  un-managed owned file (e.g. a hand-authored `.claude/settings.json`) is **refused**
  (reported), not clobbered. init surfaces refusals as a **warning** (it does not
  hard-fail, unlike `./repo generate` which exits 1) so the rest of init still runs.
  Covered by a new test `test_init_adoption_guard_warns` asserting that, with a
  pre-existing un-managed `.claude/settings.json`, init reports a refusal, does **not**
  raise `SystemExit`, leaves the file untouched, and still runs the remaining template
  generators.

## Acceptance criteria

Criteria are shell commands run via the repo tooling, **re-run by the impl gate**.
Written verbatim in this fenced block (not a markdown table) so they execute exactly
as shown.

```bash
# IMS-C1 (IMS-1): the shared helper exists in the generation layer.
grep -qE "^def generate_surface\(" repo_tools/agent/generate.py

# IMS-C2 (IMS-1): BOTH entry points call the shared helper (no duplicated logic).
grep -q "generate_surface" repo_tools/agent/generate_cmd.py && grep -q "generate_surface" repo_tools/init.py

# IMS-C3 (IMS-2): a NEW named test asserts init emits the agent-config surface on the
#   init path. The named node fails (non-zero) if the test is ABSENT — so it is not vacuous.
PYTHONHOME= PYTHONPATH="$PWD" python -m pytest "test_driver/tests/test_init.py::test_init_generates_agent_surface" -q

# IMS-C4 (IMS-3): a NEW named test asserts a pre-existing un-managed .claude/settings.json yields
#   a refusal WARNING — init does NOT raise SystemExit, leaves the file untouched, and still runs
#   the remaining template generators. Again non-vacuous (fails if the test is absent).
PYTHONHOME= PYTHONPATH="$PWD" python -m pytest "test_driver/tests/test_init.py::test_init_adoption_guard_warns" -q

# IMS-C5 (all): the full suite passes (no regressions).
PYTHONHOME= PYTHONPATH="$PWD" python -m pytest test_driver/tests -q
```

All criteria are executable and frozen at the spec gate; the impl gate re-runs them
itself rather than trusting the authoring agent.
