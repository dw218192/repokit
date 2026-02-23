# Repokit

Repo tooling framework consumed as a git submodule. Provides a unified `./repo` CLI configured per-project via `config.yaml`.

Inspired by [NVIDIA Omniverse repo_man](https://docs.omniverse.nvidia.com/kit/docs/repo_man/latest/index.html).

## Why

A human types `./repo build`; an AI agent calls the same command. Both get platform-aware token expansion, consistent error handling, and exactly the operations the project author intended. `./repo --help` is all either needs to discover available operations.

- **One config, every platform.** Define commands once with `@filter` variants — `./repo build` resolves to the right toolchain on Windows, Linux, and macOS.
- **Discoverable by design.** `./repo --help` lists every operation. Agents don't need project-specific prompts to find the build command.
- **Zero infrastructure.** `git submodule add` + bootstrap. Works offline and in CI.

## Quick Start

```bash
git submodule add -b release https://github.com/dw218192/repokit.git tools/framework
tools/framework/bootstrap.sh   # or bootstrap.ps1 on Windows
```

Create `config.yaml` in your project root:

```yaml
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

Any `config.yaml` section with a `steps` key is automatically registered as a `./repo <name>` command — no Python required. All auto-generated commands support `--dry-run`.

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
./repo agent                                                # interactive orchestrator
./repo agent --role worker --ticket add-hierarchical-config -w   # headless worker (worktree)
./repo agent --role reviewer --ticket add-hierarchical-config    # headless reviewer
```

**Interactive mode** (no `--ticket`) opens a Claude Code session as an **orchestrator**. The orchestrator owns the full lifecycle: plan changes, create tickets, dispatch workers, merge results, and verify acceptance criteria. Bash calls are checked against `allowlist_default.toml` (deny-first, then allow). Hooks and MCP configs are written to `_agent/plugin/` via `--plugin-dir`, keeping user settings untouched. Two stdio MCP servers are always available: `coderabbit` (code review) and `tickets` (ticket CRUD).

**Headless mode** (`--role` + `--ticket`) runs `claude -p` as a subprocess, reads the ticket JSON, and returns structured output. Workers and reviewers are dispatched by the orchestrator.

```
_agent/
    tickets/            ← one JSON file per ticket
    plugin/             ← auto-generated hooks and MCP config
```

### Recommended Workflow

**Plan → ticket → execute → merge → verify.**

1. `./repo agent` — start an interactive orchestrator session.
2. Describe what you want. The orchestrator explores the codebase and enters plan mode.
3. Approve the plan. The orchestrator creates tickets with short descriptive IDs (e.g. `add-auth-hook`) via the `create_ticket` MCP tool.
4. The orchestrator dispatches headless workers and reviewers for each ticket.
5. After review passes, the orchestrator merges the worktree branch, builds, tests, and verifies acceptance criteria before moving on.

**Keep tickets small.** Each ticket should be completable in a single focused agent session. If a ticket needs too many turns, split it.

**Use worktrees for isolation.** The `-w` flag runs workers in a git worktree so they don't interfere with your working tree or each other.

**Let the orchestrator drive.** Resist the urge to implement directly in the orchestrator session — its value is in planning, dispatching, and verifying. The worker/reviewer cycle gives you built-in code review.

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
| verify → closed | yes | — | yes | `result=pass`, all criteria met |
| verify → todo | yes | — | yes | `result=fail` |

**Field permissions:**

| Field | Orchestrator | Worker | Reviewer |
|---|---|---|---|
| `status` | yes | yes | yes |
| `notes` | yes | yes | — |
| `result` | yes | — | yes |
| `feedback` | yes | — | yes |
| `description` | yes | — | — |

Agent settings in `config.yaml`:

```yaml
agent:
  allowlist: "path/to/custom_rules.toml"    # override default command allowlist
  debug_hooks: true                          # log hook decisions to _agent/hooks.log
  max_turns: 30                              # turn limit for headless agents
```

## Configuration

**Tokens** are `{placeholders}` expanded in commands:

```yaml
tokens:
  build_root: _build
  build_dir: "{build_root}/{platform}/{build_type}"   # cross-references other tokens
  install_dir:
    value: "{build_dir}/install"
    path: true                                         # normalized to forward slashes
```

**List-valued tokens** become CLI dimension flags (`--platform`, `--build-type`). Use `@filter` to vary steps by dimension:

```yaml
tokens:
  platform: [windows-x64, linux-x64, macos-arm64]
  build_type: [Debug, Release]

build:
  steps@windows-x64:
    - "msbuild {build_dir}/project.sln /p:Configuration={build_type}"
  steps@linux-x64:
    - "make -C {build_dir} -j$(nproc)"
```

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
| `run_command(cmd, log_file=, env_script=, cwd=, env=)` | Run a subprocess, optionally tee to log and source an env script |
| `CommandGroup(label, log_file=, env_script=, cwd=, env=)` | Context manager for build phases with pass/fail tracking and CI fold markers |
| `find_venv_executable(name)` | Find executable in the venv, fallback to system PATH |
| `invoke_tool(name, tokens, config, ...)` | Call another registered tool programmatically |
| `logger` | Shared colored logger (`logging.getLogger("repo_tools")`) |
| `glob_paths(pattern)` | Recursive glob returning sorted `Path` list |

## Dependencies

Framework dependencies (click, ruff, etc.) are installed by bootstrap. For project-specific deps, create `tools/requirements.txt` and re-run bootstrap.

## Versioning & Publishing

Bump `VERSION` and push to `main`. CI syncs to the `release` branch and tags `v<version>`. See [CONTRIBUTING.md](CONTRIBUTING.md).
