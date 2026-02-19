# Repokit

Repo tooling framework consumed as a git submodule. Provides a unified `./repo` CLI for build, test, format, clean, and more — configured per-project via `config.yaml`.

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

build:
  command: "cmake --build {build_dir} --config {build_type}"

test:
  command: "ctest --test-dir {build_dir} --build-config {build_type}"
```

Then run:

```
./repo build
./repo test --verbose
./repo format
./repo clean --all
```

To pin a specific repo kit framework version: `cd tools/framework && git checkout v1.0.0`

## Why

Define build/test/format commands once in `config.yaml` with `@filter` for platform-specific variants — `./repo build` works on any OS and project. AI agents can run `./repo --help` to discover available operations immediately. New contributors bootstrap once and are productive the same way.

## Built-in Tools

| Tool | Description |
|---|---|
| `build` | Run build command from config with token expansion |
| `test` | Run test command with verbose flag support |
| `format` | Format source (clang-format for C++, ruff for Python) |
| `clean` | Remove build artifacts (`--build`, `--logs`, `--all`) |
| `context` | Display resolved tokens and paths |
| `python` | Run Python in the repo tooling venv |
| `agent` | Launch coding agent with repo-aware auto-approval |

Platform and build type are auto-detected but can be overridden:

```
./repo --platform linux-x64 --build-type Release build
```

## Configuration

**Tokens** are `{placeholders}` expanded in commands. Scalar tokens are simple substitutions; some (like `{build_dir}`, `{workspace_root}`, `{repo}`) are built-in.

```yaml
tokens:
  build_root: _build        # scalar — just a value
```

**List-valued tokens** automatically become CLI flags with auto-detection. For example, listing platforms gives you `--platform`:

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

The built-in `{repo}` token expands to a cross-platform CLI invocation, so commands can nest `./repo` tools:

```yaml
test:
  command: "{repo} python -m pytest {workspace_root}/tests/"
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

## Versioning & Publishing

Bump `VERSION` and push to `main`. CI runs tests, then syncs to the `release` branch and tags as `v<version>`. See [CONTRIBUTING.md](CONTRIBUTING.md) for details on the two-branch release model.
