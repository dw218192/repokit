# Changelog

## 0.7.18

- **Agent**: Replace `{repo_cmd}` CLI references in prompt templates with MCP tool names (`repo_build`, `repo_test`, `repo_format`). Workers/reviewers in worktrees were shown a hardcoded CLI path pointing at the main workspace; the MCP tools were already worktree-aware but prompts didn't reference them. Removes `{repo_cmd}` and `{framework_root}` template variables from all role prompts.

## 0.7.17

- **Agent**: Fix `repo_cmd` MCP server using main workspace root instead of worktree — workers in worktrees were building/testing the main workspace, not their own changes. Now uses `effective_cwd` (worktree path when dispatched, project root otherwise).

## 0.7.16

- **Core**: Add `_resolve_cfg_reference()` — tool config sections can now be `{cfg:dotted.path}` strings that resolve to another config dict. Enables prebuild tools to alias their config (e.g. `slangc: "{cfg:build.prebuild.slangc}"`).
- **Clean**: Add named groups — config `groups:` dict maps group names to path lists. `./repo clean <group>` cleans only that group; no args cleans all.
- **Clean**: Add `re:` prefix for regex patterns — entries starting with `re:` are matched as regex against relative paths (walked via `os.walk`), while plain entries use glob. Enables precise exclusion without a separate `exclude` config.
- **Clean**: Protect `_agent` directory — added to `PROTECTED` set alongside `.git` to prevent accidental deletion of worktrees, tickets, and session data.

## 0.7.15

- **Agent**: Add `merged` ticket state between `verify` and `closed` — reviewer transitions `verify → merged` which auto-merges the worktree branch into the base branch, removes the worktree, and deletes the branch. Orchestrator then confirms integration (`merged → closed`) or reopens (`merged → todo`).
- **Agent**: Enforce clean worktree before verify — workers must commit all changes before transitioning to `verify`; the transition is rejected if the worktree has uncommitted changes.
- **Agent**: Block manual worktree merges — `git merge worktree-*` is denied in the allowlist; worktree branches can only be merged via the ticket state machine.
- **Agent**: Updated orchestrator, worker, and reviewer prompts to reflect the new lifecycle. Orchestrator prompt clarifies that small direct edits are allowed without tickets.

## 0.7.14

- **ShellCommand**: Auto-sanitize subprocess environment when an `env_script` is provided — `sanitized_subprocess_env()` is applied automatically to strip venv PATH/PYTHONPATH/PYTHONHOME contamination from external tool invocations. Previously each caller had to remember to pass `env=sanitized_subprocess_env()`, and forgetting caused silent crashes (e.g. slangc exit 255). Only applied when `env_script` is set (external tools), not for internal Python subprocess calls.
- **ShellCommand**: Stop silencing env script stderr — changed `>nul 2>&1` to `>nul` (Windows) and `>/dev/null 2>&1` to `>/dev/null` (Unix) so that errors from `vcvarsall.bat` or other env scripts are visible instead of silently swallowed.

## 0.7.13

- **Agent**: Support project-level allowlist extensions via `agent.allowlist_extra` in `config.yaml` — extra TOML files are merged with the framework default allowlist, allowing projects to add allow/deny rules without replacing the base set
- **Agent**: Add `--extra-rules` flag to `check_bash` hook for passing additional rule files

## 0.7.12

- **Agent**: Confine Write/Edit tools to worktree root via `check_bash` hook -- workers dispatched into worktrees are blocked from writing to the main repo or other paths outside their worktree (temp dir still allowed)
- **Agent**: Thread `agent_cwd` through CLI backend so `--project-root` for hooks points to the worktree, not the main repo

## 0.7.11

- **Agent**: Fix allowlist deny rule blocking workers from their own worktrees — `_agent/worktrees/` paths are now exempted via negative lookahead
- **Agent**: Add `stdin=subprocess.DEVNULL` to all subprocess calls in MCP handlers and CLI backend to prevent stdin inheritance from corrupting the MCP protocol stream
- **Agent**: Add optional `timeout` parameter to `dispatch_agent` MCP tool (defaults to no timeout)

## 0.7.10

- **CI**: Fix publish step failing due to dirty working tree — `repo init` generates `CLAUDE.md` during bootstrap, `git restore .` before publish

## 0.7.9

- **Agent**: Fix dispatch MCP server not registered in CLI backend — `_write_plugin()` checked `role is None` but `run_interactive()` passes `role="orchestrator"`, so the dispatch tool was never added to `.mcp.json`

## 0.7.8

- **Init**: Skip CI template generation if any workflow already exists in `.github/workflows/`, not just `ci.yml`

## 0.7.7

- **Init**: Generate `CLAUDE.md` with repokit section on `repo init` — creates the file if absent, appends if it exists without a `## Repo tooling` section, skips if already present

## 0.7.6

- **TUI**: Fix space key not working on Windows — Kitty keyboard protocol encodes space as CSI u sequence with no character, causing TextArea to silently drop it
- **TUI**: PlanApprovalBar now accepts space key in addition to Enter for plan approval

## 0.7.5

- **Init**: Generate GitHub Actions CI template (`.github/workflows/ci.yml`) on first `repo init`, skipped on subsequent runs

## 0.7.4

- **CLI backend**: Fix `FileNotFoundError` on Windows when Claude is installed via npm (`.ps1` wrapper → prefer `.cmd`)
- **TUI**: Fix crash when tool results contain markup-like characters (brackets, quotes, equals signs)
- **Token resolution**: Auto-resolve `{token}` references in tool config values — tools no longer need manual `resolve_path()` calls

## 0.7.3

- Fix linting issues

## 0.7.2

- **Format tool**: Use `git ls-files` for file discovery instead of hardcoded exclusion list. Respects `.gitignore` automatically — no more scanning uv cache, build artifacts, or agent worktrees. Falls back to rglob for non-git repos.

## 0.7.0

### Agent

- **Dual backend architecture**: Agent sessions run on either a **CLI backend** (subprocess `claude` binary) or an **SDK backend** (`claude-agent-sdk` Python API). The SDK backend runs hooks and MCP tools in-process; the CLI backend writes a plugin directory and launches `claude` as a subprocess. Auto-detection prefers SDK when installed, falls back to CLI. Headless roles (worker/reviewer) always use CLI to avoid nesting SDK sessions. Configure with `agent.backend: cli|sdk` or `--backend` flag.
- **Textual TUI**: Interactive agent sessions use a Textual-based terminal UI replacing the plain REPL. Chat log, collapsible tool call pane with syntax-highlighted arguments, diff display for Edit tool, file tree, ticket panel with color-coded status, task panel with TodoWrite integration, plan approval prompt, `/clear` command, Ctrl+C interrupt, and chat history persistence to JSONL.
- **In-process hooks** (SDK backend): PreToolUse (bash allowlist) and PermissionRequest (MCP auto-approve) hooks run as async Python callbacks instead of shell subprocess commands.
- **In-process MCP tools** (SDK backend): Lint, CodeRabbit, ticket CRUD, repo commands, dispatch, and event tools are registered via `@tool` decorator — no stdio subprocess spawns.
- **Agent dispatch MCP tool**: New `dispatch_agent` tool lets the orchestrator spawn worker/reviewer agents directly via MCP instead of going through Bash.
- **Repo command MCP tools**: `repo_cmd.py` auto-discovers config sections with `steps` (and registered `RepoTool` instances) and exposes each as a `repo_<name>` MCP tool, so agents invoke `./repo build`, `./repo test`, etc. without raw Bash.
- **Event subscriptions**: `list_events` and `subscribe_event` MCP tools. Session suspends on subscribe and resumes when the event fires. Built-in event groups: `github.check_failed` (polls `gh run list`), `github.pr_review` (polls for CHANGES_REQUESTED). Poll intervals and payload commands configurable via `agent.events` in config.
- **Ticket approval**: Optional user approval before creating tickets. Enable via `agent.human_ticket_review: true` in config.
- **Prompt injection**: Project-specific instructions appended to agent role prompts. Configure via `agent.prompts.common`, `agent.prompts.orchestrator`, etc.
- **File logging**: Agent sessions logged to `_agent/logs/<role>-<ticket>-<timestamp>.log`.

### Framework

- **`register_subcommands()` hook**: `RepoTool` subclasses can override `register_subcommands(group)` to add subcommands while still using the standard `setup()` / `default_args()` / three-way merge pipeline. `_make_tool_command()` creates a `click.Group` when this hook is overridden.
- **Agent tool uses standard pipeline**: `AgentTool` no longer overrides `create_click_command()`. Config fields `backend` and `max_turns` are exposed as `--backend` and `--max-turns` CLI flags with proper defaults < config < CLI merge.
- **Configurable config filename**: `get_config_file()` resolves the project config filename (default `config.yaml`), with an override mechanism via `{framework_root}/_managed/config_name`. `_is_repokit_config()` detects whether an existing YAML is a repokit config by checking keys against `_TOOL_REGISTRY`.
- **Config template generation**: `./repo init` generates a starter `config.yaml` when none exists. If the default filename conflicts with a non-repokit config, prompts for an alternate name and persists the override.
- **MCP package reorganization**: Standalone stdio MCP scripts replaced by `repo_tools/agent/mcp/` package with shared JSON-RPC infrastructure (`_jsonrpc.py`). Launch individual servers via `python -m repo_tools.agent.mcp <server>`.
- **New `sdk` dependency group**: `claude-agent-sdk>=0.1.0`, `textual>=3.0`, and `rich>=13.0` (install with `uv sync --group sdk`).

## 0.6.1

- **3-layer config merge**: `load_config()` now loads framework defaults (`config.defaults.yaml`) as a base layer, then project `config.yaml`, then `config.local.yaml`.
- `config.local.yaml` is now loaded even when `config.yaml` is absent (previously silently skipped).
- **`key+` list extension**: `_deep_merge` now supports a `key+` suffix to append to a base list instead of replacing it (e.g. `paths+: [extra]` extends `paths`). A `+` in the middle of a key name is not special.
- **Clean tool redesign**: Default paths (`_agent/`, `**/__pycache__`) moved to `config.defaults.yaml`. Use `paths+` in project config to extend them. `PROTECTED` reduced to `.git` only — `_tools`, `_agent`, and `node_modules` removed. Clean handles framework tool artifacts; init owns its own cleanup (`./repo init --clean`).

## 0.6.0

- **`framework_root/_managed/` layout**: All generated content (venv, `pyproject.toml`, `uv.lock`, uv binary, Python installations, cache) now lives under `framework_root/_managed/` instead of split across `_tools/` and `tools/`. The framework's own `.gitignore` handles `_managed/`, simplifying the consumer's `.gitignore`. This allows the framework submodule to be placed anywhere (e.g. `dev_tools/blah/framework`), not just `tools/framework`.
- **`tools_dir` and `managed_dir` built-in tokens**: New reserved tokens for referencing the tools directory (`framework_root.parent`) and managed directory (`framework_root/_managed`).
- **Framework-at-root validation**: Bootstrap and CLI reject placing the framework directly at the workspace root with a clear error message.
- **Project tool discovery**: `cli.py` derives the project tool directory from `framework_root.parent` instead of hardcoding `workspace_root/tools`.
- Fix allowlist: `git rebase --continue`, `--abort`, and `--skip` are now permitted (recovery commands).

## 0.5.1

- `{cfg:...}` now supports arbitrary nesting depth (e.g. `{cfg:repo.tokens.unity_project}`).
- `clean` tool: config `paths` append to defaults instead of replacing them.

## 0.5.0

- **`{cfg:section.key}` config cross-references**: Token templates can now reference values from other config sections. `{cfg:package.output_dir}` resolves to `config["package"]["output_dir"]`. Values are transitively expanded through the normal multi-pass token resolver.
- **`{env:VAR_NAME}` inline env var access**: Lightweight alternative to declaring env-backed tokens in `repo.tokens`. `{env:UNITY_EDITOR}` resolves to the environment variable directly.
- **`./repo clean` tool**: Built-in tool for removing build artifacts. Defaults to `_build/`; configure additional paths in `clean.paths`. Supports glob patterns, `{cfg:...}` cross-refs, `--dry-run`, and safety checks (protected dirs, workspace boundary).

## 0.4.1

- Fix: config sections with `steps:` now override same-named built-in framework tools (e.g. `package`). Previously the built-in tool always ran, ignoring the user's custom steps. Priority order: project Python tools > config `steps:` > framework tools.

## 0.4.0

- **Env-var-backed tokens**: Dict tokens now support an `env` key that resolves the value from an environment variable at runtime, with optional `value` fallback. Combine with `path: true` for cross-platform tool paths (e.g., `UNITY_EDITOR`).
- **`config.local.yaml` overlay**: A git-ignored `config.local.yaml` is deep-merged on top of `config.yaml` for machine-specific overrides. Dicts merge recursively (local wins); lists and scalars are replaced. `./repo init` automatically adds it to `.gitignore`.

## 0.3.9

- `ShellCommand` class replaces `run_command()`. Separates command preparation (env-script wrapping, suffix resolution, env merging) from execution: `.run(**kw)` returns `CompletedProcess`, `.popen(**kw)` returns `Popen`, `.exec(log_file=)` provides fail-loud semantics with optional log tee.
- Removed `run_command()` — all callers migrated to `ShellCommand`.

## 0.3.8

- Fix `run_command` env_script sourcing on POSIX: use `.` instead of `source` (bashism) so it works with `/bin/sh` (dash) on Ubuntu.

## 0.3.7

- Remove hardcoded dimension special-casing from CLI: delete `_auto_detect_dimension` and `normalize_build_type` usage. Dimensions are now fully generic — first list item is the default, `Click.Choice(case_sensitive=False)` handles normalization.
- Rename `normalize_build_type` → `to_cmake_build_type` in `core.py`.

## 0.3.6

- Remove platform-specific `os.execvp` path in interactive agent mode; use `subprocess.run` + `sys.exit` on all platforms.

## 0.3.5

- Automatic worktree lifecycle: worktrees are created on agent dispatch (no `-w` flag needed) and auto-cleaned when a ticket reaches `closed`. Branch name is deterministic: `worktree-<ID>`. The `worktree_branch` field is stamped on the ticket when it first leaves `todo`. `./repo agent worktree remove <ID>` retained as escape hatch.
- Extracted `repo_tools/agent/worktree.py` module for shared worktree helpers.

## 0.3.4

- `PackageTool`: declarative glob-mapping tool that collects build outputs into a package directory. Supports token expansion in `src` patterns, `{a,b}` brace expansion, `optional` mappings, `--dry-run`, and fail-loud on zero matches.

## 0.3.3

- `sanitized_subprocess_env()` utility: returns env overrides that strip the repo-shim's `PYTHONPATH`, `PYTHONHOME`, and venv `PATH` entries, preventing Python version contamination in Conan/CMake subprocesses.

## 0.3.1

- Fix reviewer structured output: add `criteria` boolean array to reviewer JSON schema so criteria are marked via structured output, not just MCP calls.
- Fix silent failure: when ticket update fails (e.g. unmet criteria blocking `verify -> closed`), the returned JSON now includes an `"error"` key instead of silently reporting success.

## 0.3.0

**Feature-based optional dependencies.**

- **Reserved `repo` section.** `config.yaml` now has a clear split: the `repo` section holds framework settings (tokens, features), and every other section with `steps` becomes a `./repo <name>` tool. Tokens moved from top-level `tokens:` to `repo.tokens:`.
- **Feature groups.** New `repo.features` list controls which optional dependency groups are installed. Feature groups (`cpp`, `python`) are defined as PEP 735 `[dependency-groups]` in the framework's `pyproject.toml`. When `features` is omitted, all groups are installed.
- **Feature-gated tools.** `RepoTool` gains a `feature` attribute. Tools tied to a feature are hidden from `./repo --help` when that feature is not enabled.
- **`pyproject.toml` as single dependency source of truth.** Bootstrap extracts core deps directly from `pyproject.toml` — no separate `requirements.txt`.
- **`repo init` generates `tools/pyproject.toml`.** Merges core deps + enabled feature groups + user project deps, then runs `uv sync`. User deps in `[dependency-groups].project` are preserved across regeneration.
- **`require_executable()` / `find_executable()`.** New helpers in `repo_tools/features.py` replace ad-hoc `shutil.which()` checks. `require_executable` exits with a message telling the user which feature to enable.
- **Bootstrap calls `repo init`.** Bootstrap scripts now install only core deps, then call `./repo init` for feature-based install.
- **Version in `pyproject.toml`.** Removed standalone `VERSION` file; `publish` tool reads from `pyproject.toml`.

## 0.2.9

- `repo init` command for dependency installation and `.gitignore` patching.
- Reviewer pre-flight check: reviewer refuses to start if no reviewable diff exists.
- Fix reviewer bypass when ticket was already in `verify` state.

## 0.2.8

- Orchestrator prompt: ticket lifecycle, dispatch sequence.
- Lint MCP server (clang-tidy + ruff) as stdio plugin.

## 0.2.7

- Agent allowlist: `dir = "project_root"` constraint for scoped commands.
- Ticket reopen: `verify -> todo` transition with `result=fail`.

## 0.2.6

- Fix publication workflow edge cases.

## 0.2.5

- `steps` redesign: `command` key replaced by `steps` list (string or object with `command`, `cwd`, `env_script`, `env`).
- `@filter` variants for platform-specific steps.
- `env_script` support for sourcing environment files before commands.

## 0.2.4

- Ticket MCP: `mark_criteria`, `delete_ticket`, corruption handling, required criteria fields.
- Role-based field permissions for ticket updates.

## 0.2.3

- Command lists: multiple commands per step.
- `env_script` fail-loud behavior.
- `cwd` parameter for `CommandRunner`.

## 0.2.2

- `glob_paths()` utility for recursive file matching.
- `CommandGroup` context manager for build phases with pass/fail tracking.

## 0.2.1

- Agent: ticket-driven planning with MCP server.
- Pre-tool Bash hooks with allowlist checking.
- CodeRabbit MCP server for automated code review.

## 0.2.0

- Agent team: orchestrator, worker, reviewer roles.
- Stdio MCP servers for ticket CRUD and code review.
- Bash command allowlist with deny-first evaluation.

## 0.1.1

- Publish tool for two-branch release model.
- `find_venv_executable()` helper.

## 0.1.0

- Initial release: `./repo` CLI, `config.yaml`, token expansion, format tool, bootstrap scripts.
