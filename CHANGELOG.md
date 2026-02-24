# Changelog

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
