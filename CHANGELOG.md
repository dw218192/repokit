# Changelog

## 0.7.0

- **Agent SDK backend**: Replace CLI subprocess (`subprocess.run("claude", ...)`) with `claude-agent-sdk` Python API. Headless mode uses `query()`, interactive mode uses `ClaudeSDKClient` REPL with Rich rendering.
- **In-process hooks**: PreToolUse (bash allowlist) and PermissionRequest (MCP auto-approve) hooks run as Python async callbacks instead of shell subprocess commands.
- **In-process MCP tools**: Lint, CodeRabbit, and ticket CRUD tools are registered via SDK `@tool` decorator, eliminating three stdio subprocess spawns per agent session.
- **New `sdk` dependency group**: `claude-agent-sdk>=0.1.0` and `rich>=13.0` (install with `uv sync --group sdk`).
- **`register_subcommands()` hook**: `RepoTool` subclasses can override `register_subcommands(group)` to add subcommands while still using the standard `setup()` / `default_args()` / three-way merge pipeline. `_make_tool_command()` creates a `click.Group` when this hook is overridden.
- **Agent tool uses standard pipeline**: `AgentTool` no longer overrides `create_click_command()`. Config fields `backend` and `max_turns` are now exposed as `--backend` and `--max-turns` CLI flags with proper defaults < config < CLI merge.

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
