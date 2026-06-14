# Spec — Allowlist collapse (zero custom permission code)

Status: draft (pending spec-gate). Work item: `allowlist-collapse`. Implements ADR-3.
Branch: `work/allowlist-collapse`.

## Goal

Remove the custom Bash/PowerShell allowlist entirely — `allowlist-collapse` is a
**removal, not a rewrite** (ADR-3). After this work item there is **zero custom
permission code**: outward-action safety is the native auto-mode classifier
(user-owned), and the only repokit-emitted permission config is the native
`permissions.deny` block + the ADR-immutability hook already produced by the
Phase-A generation layer.

## Non-goals

- **Removing the dispatch/headless backend itself** — that is `scrap-dispatch-headless`.
  But see *Sequencing*: the allowlist hooks are wired only by that backend, so the
  two are coupled and land in the same batch.
- **Removing the `check_bash` / `approve_mcp` wiring from `_write_plugin`
  (`claude/_cli.py`)** — that file is deleted wholesale by `scrap-dispatch-headless`,
  so the wiring removal is *its* outcome, not independently attributable here. This
  work item owns only the allowlist *modules* and their *dispatch branches* in
  `hooks/__init__.py`.
- **Removing `roles` as an identity axis wholesale** — the ticket-FSM role
  constants (`_ROLE_ALLOWED_*`) are `roles-to-subagents` / ticket removal. This work
  item removes only the **`roles=` rule filter** that lives in the allowlist.
- Changing the integrity stack (ADR-2): executed criteria + human sign-off are
  untouched; the denies are best-effort prevention, never the integrity guarantee.

## Evidence

- The native replacement suffices and already exists: **SPIKE-1** (concluded) +
  `repo_tools/agent/generate.py::_render_settings_json` (emits `permissions.deny`
  for `docs/adr/**` and `_managed/**` + the `adr_immutable` PreToolUse hook).
- Decision + rationale: **ADR-3**; kill-list framing: `demolition-sequence.md` (DEM-4).
- Coupling (cited code): `rules.py`'s only *Python importer* is `hooks/check_bash.py`
  (`from ..rules import check_command, load_rules`, check_bash.py:30). Its broader
  reference surface — which C-5 (full suite) must also clear — is: the `rules_path`
  threaded as a hook CLI argument by `_write_plugin` (`claude/_cli.py:67`), and ~5
  test files under `test_driver/tests/test_agent/` (test_rules_advanced, test_hook,
  test_cli_backend, test_tool_functions, test_approver), which are deleted here.
  The `check_bash` / `approve_mcp` hooks are wired into the agent **only** by the
  dying `_write_plugin` (`claude/_cli.py:65-105`) and dispatched in
  `hooks/__init__.py` (the `check_bash` / `approve_mcp` branches).

## Sequencing (feasibility)

Because `check_bash` / `approve_mcp` are wired solely by `_write_plugin` (the CLI
backend that dies in `scrap-dispatch-headless`), removing the allowlist modules
while that backend still emits those hook commands would leave the generated plugin
referencing dead subcommands. Therefore `allowlist-collapse` is **sequenced with
`scrap-dispatch-headless`** (same demolition batch): the backend's hook wiring is
removed in the same change set that deletes the allowlist modules. The native
denies (Phase A) are already emitted, so no permission-coverage window opens.

## Spec items

- **ALC-1** Delete `repo_tools/agent/rules.py` (bashlex/pygments AST parser, the
  `Rule.roles` `roles=` filter, `load_rules`/`check_command`).
- **ALC-2** Delete `repo_tools/agent/allowlist_default.toml` (the allow/deny manifest).
- **ALC-3** Delete `repo_tools/agent/hooks/check_bash.py` and
  `repo_tools/agent/hooks/approve_mcp.py`, and their dispatch branches in
  `repo_tools/agent/hooks/__init__.py`. (The `adr_immutable` branch and hook stay.)
- **ALC-4** No dangling imports or references to the removed modules remain in
  non-test code; the test suite passes (allowlist-specific tests are removed, not
  weakened). The `_write_plugin` wiring that referenced these hooks is removed by
  `scrap-dispatch-headless` in the same batch (see Non-goals / Sequencing).

(Item IDs are contiguous ALC-1…ALC-4. An earlier draft had a fifth item for the
`_write_plugin` wiring removal; that outcome was reassigned to
`scrap-dispatch-headless` — see Non-goals — so it is intentionally not an item here.)

## Acceptance criteria

Criteria are shell commands, run via the repo tooling and **re-run by the impl
gate** itself. They are written verbatim in this fenced block — *not* a markdown
table — so the `|` alternation in the `grep` patterns is literal and the frozen
criterion executes exactly as written (a table cell would force `\|` escaping and
make the executed form ambiguous). Each is negated where it must find **nothing**;
all are run **after** impl.

```bash
# C-1  (ALC-1, ALC-2): allowlist parser + manifest deleted
test ! -f repo_tools/agent/rules.py && test ! -f repo_tools/agent/allowlist_default.toml

# C-2  (ALC-3): allowlist hook modules deleted
test ! -f repo_tools/agent/hooks/check_bash.py && test ! -f repo_tools/agent/hooks/approve_mcp.py

# C-3  (ALC-1, ALC-4): no live importer/usage of the rules module remains.
#       Word-bounded so the unrelated local `check_command_str` is excluded.
! grep -rEn "from \.\.rules import|\bload_rules\b|\bcheck_command\b" repo_tools --include='*.py'

# C-4  (ALC-3): the check_bash/approve_mcp dispatch branches are gone from the
#       hook entrypoint. Scoped to hooks/__init__.py — the branches THIS work item
#       owns; the _write_plugin wiring in claude/_cli.py is scrap-dispatch-headless's.
! grep -En '"check_bash"|"approve_mcp"' repo_tools/agent/hooks/__init__.py

# C-5  (ALC-4): full suite passes; catches any remaining dangling reference.
./repo test
```

All criteria are executable and frozen at the spec gate; the impl gate re-runs them
itself rather than trusting the authoring agent.
