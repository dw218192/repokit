# Repokit

Repo tooling framework consumed as a git submodule. Provides a unified `./repo` CLI configured per-project via `config.yaml`.

Inspired by [NVIDIA Omniverse repo_man](https://docs.omniverse.nvidia.com/kit/docs/repo_man/latest/index.html).

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
./repo build --dry-run      # print resolved command without executing
./repo test
./repo format
```

To pin a specific repo kit framework version: `cd tools/framework && git checkout v1.0.0`

## Why

Define build/test/format commands once in `config.yaml` with `@filter` for platform-specific variants — `./repo build` works on any OS and project. AI agents can run `./repo --help` to discover available operations immediately. New contributors bootstrap once and are productive the same way.

## Tools

Any `config.yaml` section whose keys are **only** `command` (and `command@<filter>` variants) is automatically registered as a `./repo <name>` command — no Python required. Sections with extra keys are skipped with a warning; move shared values to `tokens:` or write a `RepoTool` subclass.

Every auto-generated command supports:
- `--dry-run` — print the resolved command without executing

Dimension tokens (platform, build type, etc.) are set at the group level and apply to all tools:
```
./repo --build-type Release build --dry-run
```

Framework tools with non-trivial logic:

| Tool | Description |
|---|---|
| `format` | Format source (clang-format for C++, ruff for Python) |
| `context` | Display resolved tokens and paths |
| `python` | Run Python in the repo tooling venv |
| `agent` | Launch coding agent with repo-aware auto-approval |

## Configuration

**Tokens** are `{placeholders}` expanded in commands. Everything else is user-defined — define paths, flags, or any values you need:

```yaml
tokens:
  build_root: _build                         # scalar value
  build_dir: "{build_root}/{platform}/{build_type}"  # cross-reference
```

Two tokens are always injected by the framework and **cannot** be overridden in `tokens:`:

| Token | Expands to |
|---|---|
| `{workspace_root}` | Absolute POSIX path to the project root |
| `{repo}` | Cross-platform invocation of the `./repo` CLI (`python -m repo_tools.cli …`) |

Using `{repo}` in a command lets tools call other `./repo` subcommands portably — no hardcoded `./repo` or `./repo.cmd` needed:

```yaml
test:
  command: "{repo} python -m pytest {workspace_root}/tests/"
```

**List-valued tokens** automatically become CLI flags. For example, listing platforms gives you `--platform`:

```yaml
tokens:
  platform: [windows-x64, linux-x64, macos-arm64]   # becomes --platform flag
  build_type: [Debug, Release]                        # becomes --build-type flag
```

Use `@filter` to vary config by the selected value:

```yaml
build:
  command@windows-x64: "msbuild {build_dir}/project.sln /p:Configuration={build_type}"
  command@linux-x64: "make -C {build_dir} -j$(nproc)"
```

## Extending

Tools are discovered via the `repo_tools` [namespace package](https://packaging.python.org/en/latest/guides/packaging-namespace-packages/). Place project tools in `tools/repo_tools/` (no `__init__.py`):

```
your-project/
  tools/
    framework/      # repokit submodule
    repo_tools/     # project tools — no __init__.py
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
        cmd = click.option("--verbose", is_flag=True, help="Verbose output")(cmd)
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        return {"verbose": False}

    def execute(self, ctx: ToolContext, args: dict) -> None:
        if args.get("verbose"):
            logger.info(f"workspace: {ctx.workspace_root}")
```

CLI flags map 1:1 to `config.yaml` fields under the tool name. For the tool above, you could set a default in config:

```yaml
my-tool:
  verbose: true
```

Precedence: tool defaults < config values < CLI flags.

Project tools override framework tools of the same name.

## Dependencies

Framework dependencies (click, ruff, etc.) are installed automatically by bootstrap. To add project-specific deps, create `tools/requirements.txt` and re-run bootstrap.

## Future Improvements

- **Per-tool dependency isolation**: Currently all tools share a single venv and their dependencies are merged into it. A future improvement would be strongly isolated per-tool environments to avoid conflicts between tool dependencies.
- **uv-based dependency management**: `uv` is currently used only to provision the Python runtime. Extending it to manage tool dependencies would be straightforward, but requires solving how to merge dependencies from consumer tools (project-side `tools/requirements.txt`) with framework dependencies cleanly.

## Versioning & Publishing

Bump `VERSION` and push to `main`. CI runs tests, then syncs to the `release` branch and tags as `v<version>`. See [CONTRIBUTING.md](CONTRIBUTING.md) for details on the two-branch release model.
