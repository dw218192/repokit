# Repokit

Repo tooling framework consumed as a git submodule. Provides a unified `./repo` CLI for build, test, format, clean, and more — configured per-project via `config.yaml`.

Inspired by [NVIDIA Omniverse repo_man](https://docs.omniverse.nvidia.com/kit/docs/repo_man/latest/index.html).

## Use Cases

**Cross-platform, cross-project dev commands and AI agent prompts.** Define build/test/format commands once in `config.yaml` with `@filter` for platform-specific variants — e.g. `./repo build` works on any OS and project. AI agents can run `./repo --help` to discover available operations immediately — no need to write CLAUDE.md files or agent rules from scratch. New contributors bootstrap once and are productive the same way.

## Setup

```bash
git submodule add -b release https://github.com/dw218192/repokit.git tools/framework
tools/framework/bootstrap.sh   # or bootstrap.ps1 on Windows
```

This tracks the tip of the `release` branch and creates a `./repo` shim (`repo.cmd` + `repo` bash shim on Windows) in your project root. Bootstrap installs framework deps automatically; add project-specific deps in `tools/requirements.txt`.

To pin a specific version instead:

```bash
cd tools/framework && git checkout v1.0.0
```

## Usage

```
./repo --help
./repo build
./repo test --verbose
./repo format
./repo clean --all
./repo context --json
```

Dimensions are auto-detected but can be overridden:

```
./repo --platform linux-x64 --build-type Release build
```

## Tools

| Tool | Description |
|---|---|
| `build` | Run build command from config with token expansion |
| `test` | Run test command with verbose flag support |
| `format` | Format source (clang-format for C++, ruff for Python) |
| `clean` | Remove build artifacts (`--build`, `--logs`, `--all`) |
| `context` | Display resolved tokens and paths |
| `python` | Run Python in the repo tooling venv |
| `agent` | Launch coding agent with repo-aware auto-approval |

## Configuration

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

Tokens like `{build_dir}`, `{platform}`, `{build_type}` are resolved automatically. List-valued tokens become **dimensions** with CLI flags and auto-detection.

The built-in `{repo}` token expands to a cross-platform CLI invocation, so commands can nest `./repo` tools:

```yaml
test:
  command: "{repo} python -m pytest {workspace_root}/tests/"
```

Use `@filter` syntax for dimension-specific values:

```yaml
build:
  command@windows-x64: "msbuild {build_dir}/project.sln /p:Configuration={build_type}"
  command@linux-x64: "make -C {build_dir} -j$(nproc)"
```

## Extending

Tools are discovered via the `repo_tools` [namespace package](https://packaging.python.org/en/latest/guides/packaging-namespace-packages/). Any directory containing a `repo_tools/` folder (without `__init__.py`) that's on the Python path will contribute tools. The default layout from bootstrap:

```
your-project/
  tools/
    framework/      # repokit submodule — provides repo_tools/
    repo_tools/     # project tools — no __init__.py
      my_tool.py
```

You can organize this however you like — the only requirement is that both `repo_tools/` directories are discoverable. The `tools/repo_tools/` path is checked by default.

Each file should define a `RepoTool` subclass:

```python
from repo_tools.core import RepoTool, ToolContext, logger

class MyTool(RepoTool):
    name = "my-tool"
    help = "Does something useful"

    def execute(self, ctx: ToolContext, args: dict) -> None:
        logger.info(f"workspace: {ctx.workspace_root}")
```

Project tools are auto-discovered and override framework tools of the same name.

## Versioning & Publishing

The `release` branch is the published artifact — consumers point their submodule at it. Each commit on `release` is a tagged version.

To publish: bump the `VERSION` file and push to `main`. CI runs tests via `./repo test`, then automatically runs `./repo publish` to sync `main` to `release` (excluding dev-only files), commit, and tag as `v<version>`.
