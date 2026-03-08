# Repokit

Repo tooling framework consumed as a git submodule. Provides a unified `./repo` CLI configured per-project via `config.yaml`.

Inspired by [NVIDIA Omniverse repo_man](https://docs.omniverse.nvidia.com/kit/docs/repo_man/latest/index.html).

## Why

A human types `./repo build`; an AI agent calls the same command. Both get platform-aware token expansion, consistent error handling, and exactly the operations the project author intended. `./repo --help` is all either needs to discover available operations.

- **One config, every platform.** Define commands once with `@filter` variants — `./repo build` resolves to the right toolchain on Windows, Linux, and macOS.
- **Discoverable by design.** `./repo --help` lists every operation. Agents don't need project-specific prompts to find the build command.
- **Zero infrastructure.** `git submodule add` + bootstrap. Works offline and in CI.
- **Agent guardrails.** `./repo agent` runs AI coding agents with a Bash command allowlist, ticket-driven planning, and automated review via clang-tidy, ruff, and CodeRabbit — keeping output consistent with the plan.

## Quick Start

```bash
git submodule add -b release https://github.com/dw218192/repokit.git tools/framework
tools/framework/bootstrap.sh   # or bootstrap.ps1 on Windows
```

The submodule can live at any path — bootstrap uses `git rev-parse` to find the project root. If auto-detection fails (e.g. no git repo), pass the root explicitly:

```bash
path/to/repokit/bootstrap.sh /path/to/project
```

To wipe all bootstrap artifacts (`_tools/`, generated files, shims) and start fresh:

```bash
tools/framework/bootstrap.sh --clean
```

Create `config.yaml` in your project root:

```yaml
repo:
  tokens:
    platform: [windows-x64, linux-x64, macos-arm64]
    build_type: [Debug, Release]
    build_root: _build
    build_dir: "{build_root}/{platform}/{build_type}"

build:
  steps:
    - "cmake --build {build_dir} --config {build_type}"

test:
  steps:
    - "ctest --test-dir {build_dir} --build-config {build_type}"
```

Then run:

```
./repo build
./repo test
./repo format
```

## Tools

`config.yaml` has two kinds of top-level sections: the reserved `repo` section (tokens, features — see [Configuration](#configuration)), and everything else. Every other section with a `steps` key becomes a `./repo <name>` command automatically — no Python required. All auto-generated commands support `--dry-run`.

`steps` is always a list. Each item is either a **string** (shorthand) or an **object** with the keys `command`, `cwd`, `env_script`, and `env`:

```yaml
deploy:
  steps:
    - command: "docker build -t myapp ."
      env:
        - "DOCKER_BUILDKIT=1"
    - command: "docker push myapp"
      cwd: "{build_dir}"
```

Framework tools with non-trivial logic:

| Tool | Description |
|---|---|
| `format` | Format source (clang-format for C++, ruff for Python) |
| `context` | Display resolved tokens and paths |
| `python` | Run Python in the repo tooling venv |
| `agent` | Launch coding agents with a command allowlist |

## Agent

The `agent` tool launches [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions with pre-approved tools and a Bash command allowlist.

```
./repo agent                                                # interactive orchestrator (TUI)
./repo agent --backend cli                                  # force CLI subprocess backend
./repo agent --role worker --ticket add-auth-hook           # headless worker (auto-worktree)
./repo agent --role reviewer --ticket add-auth-hook         # headless reviewer
```

**Two backends** are available. The **CLI backend** (default) launches the `claude` binary as a subprocess with a generated plugin directory — hooks run as shell commands and MCP tools as stdio servers. The **SDK backend** (`claude-agent-sdk`) runs everything in-process and adds features not available in the CLI backend:

| | CLI | SDK |
|---|---|---|
| Interactive UI | Claude Code's built-in terminal | Textual TUI (chat log, tool pane, ticket/task panels) |
| Hooks | Shell subprocess commands | Async Python callbacks (in-process) |
| MCP tools | Stdio subprocess servers | In-process `@tool` functions |
| Event subscriptions | — | `list_events`, `subscribe_event` (pause/resume on GitHub events) |
| Session resume | — | Session ID tracking for event-driven resume |

Auto-detection prefers SDK when installed (`uv sync --group sdk`), falls back to CLI. Headless roles (worker/reviewer) always use CLI to avoid nesting.

**Interactive mode** (no `--ticket`) opens an orchestrator session. With the SDK backend, this launches a **Textual TUI** with a chat log, collapsible tool call pane with syntax-highlighted arguments, ticket panel with color-coded status, task tracker, and plan approval prompt. With the CLI backend, you get Claude Code's standard terminal.

**Headless mode** (`--role` + `--ticket`) reads the ticket JSON and returns structured output. Workers and reviewers are dispatched by the orchestrator via the `dispatch_agent` MCP tool.

MCP tools available to the agent (registered automatically):

| Tool | Purpose | Backend |
|---|---|---|
| `dispatch_agent` | Spawn worker/reviewer agents for a ticket | both |
| `repo_<name>` | Invoke any configured `./repo` subcommand | both |
| `lint` | Run ruff / clang-tidy | both |
| `coderabbit_review` | CodeRabbit code review | both |
| Ticket CRUD | `list_tickets`, `get_ticket`, `create_ticket`, `update_ticket`, etc. | both |
| `list_events`, `subscribe_event` | Pause session until a GitHub event fires | SDK only |

```
_agent/
    tickets/            ← one JSON file per ticket
    logs/               ← session logs (role-ticket-timestamp.log)
```

### Usage

Run `./repo agent` and describe what you want. The orchestrator handles the rest — it explores the codebase, plans the changes, creates tickets, dispatches headless workers and reviewers, merges results, and verifies acceptance criteria. You only need to approve the plan when prompted.

### Ticket Lifecycle

```
todo ──→ in_progress ──→ verify ──→ closed
  ↑                        │
  └────────────────────────┘ (reopen on fail)
```

| Transition | Orchestrator | Worker | Reviewer | Constraints |
|---|---|---|---|---|
| todo → in_progress | yes | yes | — | — |
| todo → verify | yes | yes | — | — |
| in_progress → verify | yes | yes | — | — |
| verify → closed | — | — | yes | `result=pass`, all criteria met |
| verify → todo | yes | — | yes | `result=fail` |

**Field permissions:**

| Field | Orchestrator | Worker | Reviewer |
|---|---|---|---|
| `status` | yes | yes | yes |
| `notes` | yes | yes | — |
| `result` | — | — | yes |
| `feedback` | yes | — | yes |
| `description` | yes | — | — |

```yaml
agent:
  backend: sdk                               # "cli" or "sdk" (auto-detect if omitted)
  human_ticket_review: true                      # gate create_ticket with interactive approval (SDK)
  max_turns: 30                              # turn limit for headless agents
  prompts:                                   # appended to role prompts
    common: "Always use ruff for formatting."
    orchestrator: "Prefer small tickets."
  required_criteria:                         # mandatory criteria on every new ticket
    - "All existing tests still pass"
```

See [Tool configuration](#tool-configuration) for the general config model (3-way merge, `key+` extension, local overrides).

## Configuration

The `repo` section is reserved for framework settings — it is never registered as a tool. It holds token definitions (`repo.tokens`) and feature flags (`repo.features`).

**Tokens** are `{placeholders}` expanded in commands:

```yaml
repo:
  tokens:
    build_root: _build
    build_dir: "{build_root}/{platform}/{build_type}"   # cross-references other tokens
    install_dir:
      value: "{build_dir}/install"
      path: true                                         # normalized to forward slashes
    unity_editor:
      env: UNITY_EDITOR              # resolve from environment variable
      value: "/usr/bin/unity"         # fallback when env var is unset
      path: true                      # normalize slashes (applied after env resolution)
```

Dict tokens with `env` resolve from environment variables at runtime, falling back to `value` if the variable is unset. Combine with `path: true` for cross-platform path tokens.

**List-valued tokens** become CLI dimension flags (`--platform`, `--build-type`). Use `@filter` to vary steps by dimension:

```yaml
repo:
  tokens:
    platform: [windows-x64, linux-x64, macos-arm64]
    build_type: [Debug, Release]

build:
  steps@windows-x64:
    - "msbuild {build_dir}/project.sln /p:Configuration={build_type}"
  steps@linux-x64:
    - "make -C {build_dir} -j$(nproc)"
```

**Local overrides** — Create `config.local.yaml` (gitignored) for machine-specific settings. It is deep-merged on top of `config.yaml`: nested dicts merge recursively, everything else (including lists) is replaced. Use `key+` to extend a list instead of replacing it (see [Config key syntax](#config-key-syntax)).

```yaml
# config.local.yaml (not committed)
test:
  steps:
    - "{repo} python -m pytest tests/ -x --pdb"
repo:
  tokens:
    unity_editor:
      env: UNITY_EDITOR
      path: true
```

**Framework defaults** — The framework ships `config.defaults.yaml` with sensible defaults (e.g. built-in event definitions, clean paths). These form the base layer — project config extends or overrides them. Merge order: `config.defaults.yaml` ← `config.yaml` ← `config.local.yaml`.

### Tool configuration

Every tool (built-in or project-defined) owns a **top-level key** in `config.yaml` whose name matches the tool name. The entire key is passed to the tool as its config dict, after the 3-way merge.

```yaml
# config.yaml
build:                    # ← config for the "build" tool
  steps:
    - "cmake --build {build_dir}"

clean:                    # ← config for the "clean" tool
  paths:
    - "{workspace_root}/dist"

agent:                    # ← config for the "agent" tool
  backend: sdk
```

Some keys ship with **framework defaults** (defined in `config.defaults.yaml`) and are active out of the box — for example `agent.backend` defaults to `"cli"` and `clean.paths` includes `__pycache__`. Keys without a default are inert until you set them in project or local config.

Use `key+` to **extend** a default list instead of replacing it:

```yaml
clean:
  paths+:                 # append to the framework-default paths list
    - "{workspace_root}/dist"
```

### Config key syntax

Config keys support two special suffixes:

| Suffix | Meaning | Example |
|---|---|---|
| `key@filter` | Dimension-based variant — selected when the filter matches | `steps@windows-x64: [...]` |
| `key+` | List extension — append to the base layer's list | `paths+: ["{workspace_root}/dist"]` |

`@filter` is resolved after all three config layers are merged. A `+` in the middle of a key name (e.g. `c++_flags`) is **not** special — only a trailing `+` triggers list extension, and only when the value is a list.

Built-in tokens (always available, cannot be overridden):

| Token | Expands to |
|---|---|
| `{workspace_root}` | Absolute POSIX path to the project root |
| `{repo}` | Cross-platform `./repo` invocation (use in commands to call other tools portably) |
| `{framework_root}` | Absolute POSIX path to the framework submodule directory |
| `{exe_ext}` | `.exe` on Windows, empty otherwise |
| `{shell_ext}` | `.cmd` on Windows, `.sh` otherwise |
| `{lib_ext}` | `.dll` / `.dylib` / `.so` |
| `{path_sep}` | `;` on Windows, `:` otherwise |

## Extending

Place project tools in `tools/repo_tools/` (no `__init__.py` — it's a [namespace package](https://packaging.python.org/en/latest/guides/packaging-namespace-packages/)):

```
your-project/
  tools/
    framework/      # repokit submodule
    repo_tools/     # project tools
      my_tool.py
```

Each file defines a `RepoTool` subclass:

```python
import click
from repo_tools.core import RepoTool, ToolContext, logger

class MyTool(RepoTool):
    name = "my-tool"
    help = "Does something useful"

    def setup(self, cmd: click.Command) -> click.Command:
        cmd = click.option("--verbose", is_flag=True)(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {"verbose": False}

    def execute(self, ctx: ToolContext, args: dict) -> None:
        if args.get("verbose"):
            logger.info(f"workspace: {ctx.workspace_root}")
```

CLI flags map 1:1 to `config.yaml` fields under the tool name. Precedence: tool defaults < config values < CLI flags. Project tools override framework tools of the same name.

### Utilities

| Function / Class | Purpose |
|---|---|
| `ShellCommand(cmd, env_script=, env=, cwd=)` | Prepared command with `.run(**kw)`, `.popen(**kw)`, and `.exec(log_file=)` (fail-loud) |
| `CommandGroup(label, log_file=, env_script=, cwd=, env=)` | Context manager for build phases with pass/fail tracking and CI fold markers |
| `find_venv_executable(name)` | Find executable in the venv, fallback to system PATH |
| `invoke_tool(name, tokens, config, ...)` | Call another registered tool programmatically |
| `logger` | Shared colored logger (`logging.getLogger("repo_tools")`) |
| `glob_paths(pattern)` | Recursive glob returning sorted `Path` list |

## Features

`repo.features` controls which optional dependency groups are installed and which tools are visible. Feature groups are defined in the framework's `pyproject.toml` as PEP 735 `[dependency-groups]`:

| Feature | Provides |
|---|---|
| `cpp` | clang-format, clang-tidy |
| `python` | ruff |
| `sdk` | claude-agent-sdk, textual, rich (in-process agent backend) |

```yaml
repo:
  features: [python]        # install only Python tooling
```

When `features` is omitted, all groups are installed. When specified, only the listed groups are installed and only tools tied to those features appear in `./repo --help`.

Run `./repo init` after changing features to sync dependencies.

## Dependencies

All dependency specs live in the framework's `pyproject.toml` — there is no separate `requirements.txt`. Bootstrap installs [uv](https://docs.astral.sh/uv/), creates a venv at `_tools/venv/`, extracts core deps from `pyproject.toml`, and calls `./repo init`. The init command generates `tools/pyproject.toml` and runs `uv sync` to install:

1. **Core deps** (click, pyyaml, colorama, etc.) — always installed.
2. **Feature deps** — only groups listed in `repo.features` (or all groups when omitted).
3. **Project deps** — injected from two sources described below.

There are two ways to add project-specific dependencies:

**`repo.extra_deps`** in `config.yaml` — for project-level deps not tied to any tool (e.g. pytest, pytest-cov). List PEP 508 dependency strings:

```yaml
repo:
  extra_deps:
    - "pytest>=7.0"
    - "pytest-cov>=4.0"
```

**`RepoTool.deps`** class variable — for deps needed by custom Python tools in `tools/repo_tools/`. Declare them on your `RepoTool` subclass:

```python
class MyTool(RepoTool):
    name = "my-tool"
    help = "Does something useful"
    deps = ["requests>=2.28"]
```

Both sources are merged, deduplicated, and installed via `uv sync` when `./repo init` runs.

## Lifecycle

| Command | Creates / Removes | Undo with |
|---|---|---|
| `bootstrap.sh` | uv, `./repo` shims, `.gitignore` entry | `bootstrap.sh --clean` |
| `./repo init` | `_managed/venv`, `_managed/pyproject.toml`, `_managed/uv.lock` | `./repo init --clean` |
| `./repo clean` | Removes framework tool artifacts (`_agent/`, `__pycache__/`, etc.) | Re-run the tool |

Setup: `bootstrap.sh` → `./repo init` → use. Teardown: `./repo clean` → `bootstrap.sh --clean`. Each layer owns its own cleanup — `./repo clean` handles transient tool output, not init artifacts.

## Versioning & Publishing

Bump the version in `pyproject.toml` and push to `main`. CI syncs to the `release` branch and tags `v<version>`. See [CONTRIBUTING.md](CONTRIBUTING.md).
