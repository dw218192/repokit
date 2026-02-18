# Repokit Refactor Plan

## Context

Code review identified 13 issues across bugs, dead code, design problems, and robustness gaps. This plan addresses all of them in a sequenced refactor. The project has a single initial commit and no known external project tools, so breaking changes to the `RepoTool` API are acceptable.

---

## Phase 1: Safe Cleanups (no behavioral changes)

### 1a. Remove dead code from `core.py`
- Delete `_get_optional_config_value` (L184-190)
- Delete `normalize_env_config` (L589-615)
- Delete `load_conan_env` (L618-654)
- Delete `is_platform_compatible` (L440-447)
- Remove unused `Mapping` import (L16)
- Remove `Optional` import, replace usages with `| None`

### 1b. Remove dead branch in `_match_filter` — `core.py:282-284`
The `else` block under the positive-match case can never fire (the guard at L277 already guarantees `value == dim_val or value == dim_name`). Delete it.

### 1c. Move `colorama_init()` from `core.py:24` to `cli.py:main()`
Remove the top-level side effect. Add `from colorama import init as colorama_init; colorama_init()` as first lines of `main()`.

### 1d. Remove dead `subcommand` arg — `agent/tool.py`
Delete the `click.argument("subcommand", ...)` in `setup()` and the `subcommand != "run"` check in `execute()`.

### 1e. Add `tomli` compat import — `agent/approver.py`
```python
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]
```
Add `tomli>=2.0; python_version < "3.11"` to `requirements.txt`.

### 1f. Narrow exception handling — `cli.py:40-42`
Split the broad `except Exception` into `except ImportError` (debug-level, expected) and `except Exception` (warning-level, unexpected). Same pattern for tool instantiation at L53-58.

### 1g. Cache config to avoid double load — `cli.py`
Rename early config to `early_config`, reuse it in the Click callback when workspace_root matches the pre-parsed value.

---

## Phase 2: Bug Fixes

### 2a. `shlex.split()` instead of `.split()` — `build.py:36`, `test.py:37`
Immediate fix: `import shlex`, change `resolved.split()` → `shlex.split(resolved)`. (Will be subsumed by Phase 3 refactor, but fix now so it's correct at every commit.)

### 2b. `run_command` leaks `CalledProcessError` — `core.py:542`
Wrap the no-log-file path:
```python
try:
    subprocess.run(run_cmd, shell=use_shell, check=True)
except subprocess.CalledProcessError as e:
    sys.exit(e.returncode)
```

### 2c. Wire `--verbose` in TestTool — `test.py`
Read `args.get("verbose")` in `execute()`, append a configurable flag (default `--output-on-failure`) to the command string before resolution.

---

## Phase 3: Structural Refactors

### 3a. Introduce `ToolContext` dataclass — `core.py`

```python
@dataclass(frozen=True)
class ToolContext:
    workspace_root: Path
    tokens: dict[str, str]          # all resolved tokens
    config: dict[str, Any]          # full filtered config
    tool_config: dict[str, Any]     # this tool's config section
    dimensions: dict[str, str]
    passthrough_args: list[str]
```

Change `RepoTool.execute(args)` → `execute(ctx: ToolContext, args: dict[str, Any])` where `args` contains **only** tool-specific values (defaults merged with tool config merged with CLI flags). No tokens mixed in.

Update `invoke_tool` to build a `ToolContext` and call `tool.execute(ctx, args)`.

### 3b. Update `_make_tool_command` — `cli.py`
Build `ToolContext` from `ctx.obj`. Build `tool_args` from only defaults + tool_config + CLI kwargs. Call `tool.execute(context, tool_args)`.

```python
context = ToolContext(
    workspace_root=Path(ctx.obj["workspace_root"]),
    tokens=tokens,
    config=config,
    tool_config=tool_config,
    dimensions=ctx.obj["dimensions"],
    passthrough_args=list(ctx.args) if ctx.args else [],
)

args = {**tool.default_args(tokens)}
for k, v in tool_config.items():
    if k not in kwargs or kwargs[k] is None:
        args[k] = v
for k, v in kwargs.items():
    if v is not None:
        args[k] = v

tool.execute(context, args)
```

### 3c. Create `CommandRunnerTool` base — new file `command_runner.py`

```python
class CommandRunnerTool(RepoTool):
    """Base for tools that run a single configured command with token expansion."""
    config_hint: str = ""

    def setup(self, cmd):
        cmd = click.option("--build-type", "-bt", default=None, help="Build type override")(cmd)
        return cmd

    def execute(self, ctx, args):
        command = args.get("command")
        if not command:
            logger.error(f"No {self.name} command configured. Add to config.yaml:\n  {self.config_hint}")
            raise SystemExit(1)
        formatter = TokenFormatter({**ctx.tokens, **args})
        resolved = formatter.resolve(command)
        logger.info(f"Running: {resolved}")
        run_command(shlex.split(resolved))
```

### 3d. Simplify `build.py` and `test.py`

**build.py** becomes ~5 lines:
```python
class BuildTool(CommandRunnerTool):
    name = "build"
    help = "Build the project"
    config_hint = 'build:\n    command: "cmake --build {build_dir}"'
```

**test.py** adds `--verbose` wiring:
```python
class TestTool(CommandRunnerTool):
    name = "test"
    help = "Run tests"
    config_hint = 'test:\n    command: "ctest --test-dir {build_dir}"'

    def setup(self, cmd):
        cmd = super().setup(cmd)
        cmd = click.option("-v", "--verbose", is_flag=True, help="Verbose test output")(cmd)
        return cmd

    def execute(self, ctx, args):
        if args.get("verbose") and args.get("command"):
            args["command"] += " " + args.get("verbose_flag", "--output-on-failure")
        super().execute(ctx, args)
```

### 3e. Update all other tools to new `execute(ctx, args)` signature

- **`context.py`**: Use `ctx.tokens` directly instead of filtering `args` for string values.
- **`clean.py`**: Read paths from `ctx.tokens` instead of `args.get("workspace_root")`.
- **`format.py`**: Read `workspace_root`, `build_root`, `logs_root` from `ctx.tokens`. Read `verify`, `backends` from `args`.
- **`python.py`**: Use `ctx.passthrough_args` instead of `args.get("passthrough_args")`.
- **`agent/tool.py`**: Read `workspace_root` from `ctx`, `backend`/`auto_approve` from `args`.

### 3f. Fix FormatTool — `format.py`

Three changes:
1. **Binary existence check**: After `find_venv_executable("clang-format")`, verify it actually exists with `shutil.which()`. Exit with clear error if not found.
2. **Python auto-detection**: In `_run_auto_detect`, check for `pyproject.toml`/`setup.py`/`ruff.toml` and run ruff if found.
3. **Batch clang-format**: In format mode, pass multiple files per invocation (batches of ~200 for Windows cmdline limits). In verify mode, use `--dry-run --Werror` if available, fall back to per-file.

---

## Files Changed

| File | Change |
|---|---|
| `core.py` | Add `ToolContext`, delete dead code, fix `_match_filter`, fix `run_command`, move `colorama_init`, update `RepoTool.execute` signature, update `invoke_tool` |
| `cli.py` | Build `ToolContext` in callback, move `colorama_init` here, narrow exceptions, cache config |
| `command_runner.py` | **New**: `CommandRunnerTool` base with `shlex.split` |
| `build.py` | Rewrite to inherit `CommandRunnerTool`, new `execute(ctx, args)` |
| `test.py` | Rewrite to inherit `CommandRunnerTool`, wire `--verbose`, new `execute(ctx, args)` |
| `format.py` | New `execute(ctx, args)`, binary check, Python auto-detect, batch mode |
| `clean.py` | New `execute(ctx, args)` |
| `context.py` | New `execute(ctx, args)`, use `ctx.tokens` directly |
| `python.py` | New `execute(ctx, args)`, use `ctx.passthrough_args` |
| `agent/tool.py` | Remove `subcommand`, new `execute(ctx, args)` |
| `agent/approver.py` | `tomli` compat import |
| `requirements.txt` | Add `tomli` conditional dep |

## Verification

- `python -m repo_tools.cli --help` should show all tools with correct options
- `python -m repo_tools.cli context` should display resolved tokens
- `python -m repo_tools.cli context --json` should output valid JSON
- `python -m repo_tools.cli clean --dry-run --all` should show what would be removed
- `python -m repo_tools.cli format --verify` with no config should not crash (should warn or detect)
- Confirm `--verbose` flag appears in `test --help`
- Import `repo_tools.core` without side effects (no colorama init)
