# Repokit

Repo tooling framework consumed as a git submodule. Provides a unified `./repo` CLI configured per-project via `config.yaml`.

Inspired by [NVIDIA Omniverse repo_man](https://docs.omniverse.nvidia.com/kit/docs/repo_man/latest/index.html).

## Why

Think of `./repo` as a **local MCP server you get for free** — no daemon, no transport layer, no client integration. A human types `./repo build`; an AI agent calls the same command. Both get platform-aware token expansion, consistent error handling, and exactly the operations the project author intended.

- **One config, every platform.** Define commands once with `@filter` variants — `./repo build` resolves to the right toolchain on Windows, Linux, and macOS.
- **Discoverable by design.** `./repo --help` lists every operation. Agents don't need project-specific prompts to find the build command.
- **Safe agent automation.** The `agent` tool launches Claude Code sessions with a command allowlist that funnels operations through `./repo`, blocking shell escapes, network exfiltration, and env snooping.
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
  command: "cmake --build {build_dir} --config {build_type}"

test:
  command: "ctest --test-dir {build_dir} --build-config {build_type}"
```

Then run:

```
./repo build
./repo test
./repo format
```

## Tools

Any `config.yaml` section with only `command` keys is automatically registered as a `./repo <name>` command — no Python required. All auto-generated commands support `--dry-run`.

Framework tools with non-trivial logic:

| Tool | Description |
|---|---|
| `format` | Format source (clang-format for C++, ruff for Python) |
| `context` | Display resolved tokens and paths |
| `python` | Run Python in the repo tooling venv |
| `agent` | Launch coding agents with repo-aware guardrails |

## Agent

The `agent` tool launches [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions in [WezTerm](https://wezfurlong.org/wezterm/) panes with pre-approved tools and a Bash command allowlist.

```
./repo agent run                              # solo session
./repo agent team my-workstream               # multi-agent workstream
```

**Solo mode** opens a single Claude Code session. Bash calls are checked against `allowlist_default.toml` (deny-first, then allow). Hooks and MCP configs are written to `_agent/plugin/` via `--plugin-dir`, keeping user settings untouched.

**Team mode** creates `_agent/<workstream_id>/` (plan, tickets, worktrees), spawns an orchestrator, and starts an MCP server that provides `send_message` and `coderabbit_review` tools. The orchestrator reads `plan.toml`, creates tickets, and dispatches worker/reviewer agents — each in its own git worktree. Sub-agents communicate via the MCP server and cannot write to `_agent/` directly. Ctrl+C stops the server and kills all agent panes.

```
_agent/<workstream_id>/
    plan.toml           ← goals and acceptance criteria
    mcp.port            ← MCP server port (written at session start)
    tickets/            ← one TOML file per ticket
    worktrees/          ← git worktrees for workers and reviewers
```

Agent settings in `config.yaml`:

```yaml
agent:
  allowlist: "path/to/custom_rules.toml"    # override default command allowlist
  debug_hooks: true                          # log hook decisions to _agent/hooks.log
  idle_reminder_interval: 120                # seconds between idle pings (team mode)
  idle_reminder_limit: 3                     # max idle pings before killing a pane
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

**List-valued tokens** become CLI dimension flags (`--platform`, `--build-type`). Use `@filter` to vary commands by dimension:

```yaml
tokens:
  platform: [windows-x64, linux-x64, macos-arm64]
  build_type: [Debug, Release]

build:
  command@windows-x64: "msbuild {build_dir}/project.sln /p:Configuration={build_type}"
  command@linux-x64: "make -C {build_dir} -j$(nproc)"
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
| `run_command(cmd, log_file=, env_script=)` | Run a subprocess, optionally tee to log and source an env script |
| `CommandGroup(label)` | Context manager for build phases with pass/fail tracking and CI fold markers |
| `find_venv_executable(name)` | Find executable in the venv, fallback to system PATH |
| `invoke_tool(name, tokens, config, ...)` | Call another registered tool programmatically |
| `logger` | Shared colored logger (`logging.getLogger("repo_tools")`) |
| `glob_paths(pattern)` | Recursive glob returning sorted `Path` list |

## Dependencies

Framework dependencies (click, ruff, etc.) are installed by bootstrap. For project-specific deps, create `tools/requirements.txt` and re-run bootstrap.

## Versioning & Publishing

Bump `VERSION` and push to `main`. CI syncs to the `release` branch and tags `v<version>`. See [CONTRIBUTING.md](CONTRIBUTING.md).
