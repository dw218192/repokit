"""Core framework: RepoTool base, token system, config @filter resolver, utilities."""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import glob
import logging
import os
import platform
import re
import shlex
import shutil
import string
import subprocess
import sys
import time
from collections.abc import Generator
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

import click
import yaml
from colorama import Fore, Style


# ── Logging ──────────────────────────────────────────────────────────


def _level_color(levelno: int) -> str:
    if levelno >= logging.ERROR:
        return Fore.RED
    if levelno >= logging.WARNING:
        return Fore.YELLOW
    return Fore.CYAN


class ToolFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = _level_color(record.levelno)
        message = record.getMessage()
        return f"{color}[{record.levelname.lower()}]{Style.RESET_ALL} {message}"


logger = logging.getLogger("repo_tools")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(ToolFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


# ── Token System ─────────────────────────────────────────────────────


class TokenFormatter(string.Formatter):
    """Format string subclass with circular-reference detection.

    Tokens can reference other tokens: ``{conan_deps_root}`` may expand
    to ``{build_root}/deps``.  This formatter recursively resolves until
    stable, but raises on cycles.
    """

    MAX_DEPTH = 10

    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def resolve(self, template: str) -> str:
        result = template
        for _ in range(self.MAX_DEPTH):
            try:
                expanded = result.format_map(self._tokens)
            except KeyError as exc:
                missing = exc.args[0] if exc.args else "unknown"
                raise KeyError(f"Missing token: {missing}") from exc
            if expanded == result:
                return expanded
            result = expanded
        remaining = _extract_references(result)
        raise ValueError(
            f"Token expansion exceeded {self.MAX_DEPTH} iterations"
            f" (unresolved: {', '.join(sorted(remaining))})"
        )


def posix_path(p: str) -> str:
    """Normalize path to forward slashes (safe for shlex.split on Windows)."""
    return p.replace("\\", "/")


# Built-in tokens resolved from the runtime environment.
def _builtin_tokens() -> dict[str, str]:
    system = platform.system()
    is_win = system == "Windows"
    is_mac = system == "Darwin"
    # Framework root: parent of the repo_tools package (the submodule dir).
    framework_root = posix_path(str(Path(__file__).resolve().parent.parent))
    return {
        "exe_ext": ".exe" if is_win else "",
        "shell_ext": ".cmd" if is_win else ".sh",
        "lib_ext": ".dll" if is_win else (".dylib" if is_mac else ".so"),
        "path_sep": ";" if is_win else ":",
        "repo": f'"{posix_path(sys.executable)}" -m repo_tools.cli --workspace-root "{{workspace_root}}"',
        "framework_root": framework_root,
    }


# Tokens set by the framework that config.yaml must not override.
_RESERVED_TOKENS = {"workspace_root", "repo", "framework_root"}


def _extract_references(template: str) -> set[str]:
    """Return the set of token names referenced by ``{name}`` placeholders.

    Uses ``string.Formatter().parse()`` which correctly ignores escaped
    braces (``{{``/``}}``), returning ``field_name=None`` for those.
    """
    refs: set[str] = set()
    for _, field_name, _, _ in string.Formatter().parse(template):
        if field_name is not None:
            refs.add(field_name)
    return refs


def _validate_token_graph(tokens: dict[str, str]) -> None:
    """Validate the token dependency graph before expansion.

    Raises:
        ValueError: on self-references or cycles (with the cycle path).
        KeyError: when a token references an undefined token.
    """
    # Build dependency graph: token -> set of tokens it depends on
    graph: dict[str, set[str]] = {}
    for name, value in tokens.items():
        refs = _extract_references(str(value))
        graph[name] = refs

        # Self-reference check (clear message before TopologicalSorter)
        if name in refs:
            raise ValueError(f"Token '{name}' references itself")

    # Missing reference check
    all_names = set(tokens)
    for name, refs in graph.items():
        missing = refs - all_names
        if missing:
            raise KeyError(
                f"Token '{name}' references undefined token(s): "
                + ", ".join(sorted(missing))
            )

    # Cycle detection via topological sort
    ts = TopologicalSorter(graph)
    try:
        ts.prepare()
    except CycleError as exc:
        # exc.args[1] is the cycle as a tuple, e.g. ('a', 'b', 'c', 'a')
        cycle = exc.args[1] if len(exc.args) > 1 else ()
        path = " -> ".join(str(n) for n in cycle)
        raise ValueError(f"Circular token reference: {path}") from exc


def resolve_tokens(
    workspace_root: str,
    config: dict[str, Any],
    dimension_values: dict[str, str],
) -> dict[str, str]:
    """Build the full token dictionary.

    Merge order (later wins):
      1. Built-in tokens (exe_ext, shell_ext, repo, etc.)
      2. Variable tokens from config
      3. workspace_root path token
      4. Dimension values (platform, build_type, etc.)
    """
    tokens: dict[str, str] = _builtin_tokens()

    # Variable tokens from config (repo.tokens section)
    repo_section = config.get("repo", {})
    if not isinstance(repo_section, dict):
        repo_section = {}
    for key, value in repo_section.get("tokens", {}).items():
        if isinstance(value, list):
            continue  # dimension tokens handled elsewhere
        if key in _RESERVED_TOKENS:
            logger.warning(f"'{key}' is a reserved token and cannot be overridden in config.")
            continue
        if isinstance(value, dict):
            raw = str(value.get("value", ""))
            if value.get("path"):
                raw = posix_path(raw)
            tokens[key] = raw
        else:
            tokens[key] = str(value)

    # workspace_root is always set from the runtime environment
    tokens["workspace_root"] = posix_path(workspace_root)

    # Dimension values override
    tokens.update(dimension_values)

    # Validate graph before expansion
    _validate_token_graph(tokens)

    # Resolve any cross-references in variable tokens
    formatter = TokenFormatter(tokens)
    resolved: dict[str, str] = {}
    for key, value in tokens.items():
        if "{" in str(value):
            try:
                resolved[key] = formatter.resolve(str(value))
            except (KeyError, ValueError) as exc:
                logger.warning("Token '%s' could not be resolved: %s", key, exc)
                resolved[key] = str(value)
        else:
            resolved[key] = str(value)

    return resolved


# ── Config Loading & @filter ─────────────────────────────────────────


def load_config(workspace_root: str) -> dict[str, Any]:
    """Load config.yaml from workspace root."""
    config_path = Path(workspace_root) / "config.yaml"
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TypeError("config.yaml must contain a top-level mapping.")
    return data



def resolve_filters(config: dict[str, Any], dimension_values: dict[str, str]) -> dict[str, Any]:
    """Walk config dict, resolve ``key@filter`` entries.

    Filter syntax:
    - ``@value`` — matches any dimension whose current value equals *value*
    - ``@val1,val2`` — AND across different dimensions
    - ``@!value`` — negation
    - ``@val1,!val2`` — compound

    More-specific filters (more conditions) win over less-specific ones.
    """
    # Build reverse lookup: value -> dimension name
    dim_lookup: dict[str, str] = {}
    for dim_name, dim_val in dimension_values.items():
        dim_lookup[dim_val] = dim_name

    return _walk_filters(config, dimension_values, dim_lookup)


def _walk_filters(
    obj: Any,
    dim_values: dict[str, str],
    dim_lookup: dict[str, str],
) -> Any:
    if isinstance(obj, dict):
        # Collect base keys and filtered keys
        base: dict[str, Any] = {}
        filtered: dict[str, list[tuple[str, int, Any]]] = {}  # base_key -> [(filter, specificity, value)]

        for key, value in obj.items():
            if "@" in str(key):
                parts = str(key).split("@", 1)
                base_key = parts[0]
                filter_str = parts[1]
                match, specificity = _match_filter(filter_str, dim_values, dim_lookup)
                if match:
                    filtered.setdefault(base_key, []).append((filter_str, specificity, value))
            else:
                base[key] = value

        # Resolve: most-specific filter wins over base
        result: dict[str, Any] = {}
        for key, value in base.items():
            if key in filtered:
                # Pick most specific
                candidates = filtered.pop(key)
                candidates.sort(key=lambda x: x[1], reverse=True)
                result[key] = _walk_filters(candidates[0][2], dim_values, dim_lookup)
            else:
                result[key] = _walk_filters(value, dim_values, dim_lookup)

        # Remaining filtered keys with no base
        for key, candidates in filtered.items():
            candidates.sort(key=lambda x: x[1], reverse=True)
            result[key] = _walk_filters(candidates[0][2], dim_values, dim_lookup)

        return result

    if isinstance(obj, list):
        return [_walk_filters(item, dim_values, dim_lookup) for item in obj]

    return obj


def _match_filter(
    filter_str: str,
    dim_values: dict[str, str],
    dim_lookup: dict[str, str],
) -> tuple[bool, int]:
    """Check if a filter matches the current dimension values.

    Returns ``(matches, specificity)`` where specificity = number of conditions.
    """
    conditions = [c.strip() for c in filter_str.split(",") if c.strip()]
    if not conditions:
        return True, 0

    for cond in conditions:
        negate = cond.startswith("!")
        value = cond.lstrip("!")

        # Find which dimension this value belongs to
        matched_any = False
        for dim_name, dim_val in dim_values.items():
            if value == dim_val or value == dim_name:
                matched_any = True
                if negate:
                    if value == dim_val:
                        return False, 0  # Negation failed
                break

        # Also check if value is a known dimension value (not current)
        if not matched_any:
            if value in dim_lookup:
                # It's a known dimension value but not the current one
                if negate:
                    pass  # !other_value is true (we don't have that value)
                else:
                    return False, 0  # Wanted a value we don't have
            else:
                # Unknown value — treat as no match for positive, match for negative
                if not negate:
                    return False, 0

    return True, len(conditions)


# ── ToolContext ───────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class ToolContext:
    """Immutable context passed to every tool execution."""

    workspace_root: Path
    tokens: dict[str, str]
    config: dict[str, Any]
    tool_config: dict[str, Any]
    dimensions: dict[str, str]
    passthrough_args: list[str]


# ── RepoTool Base ────────────────────────────────────────────────────


class RepoTool:
    """Base class for all repo tools.

    Subclasses set ``name`` and ``help``, then implement ``setup()`` to
    add click options and ``execute()`` to run the tool.
    """

    name: str = ""
    help: str = ""
    feature: str = ""
    deps: list[str] = []

    def setup(self, cmd: click.Command) -> click.Command:
        """Add click options/arguments to the command. Return the command."""
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        """Return default args dict before config/CLI merge."""
        return {}

    def create_click_command(self) -> click.BaseCommand | None:
        """Override to provide a custom Click group/command. Returns None by default."""
        return None

    def execute(self, ctx: ToolContext, args: dict[str, Any]) -> None:
        """Execute the tool with context and tool-specific args."""
        raise NotImplementedError


# ── Tool Registry ────────────────────────────────────────────────────

_TOOL_REGISTRY: dict[str, RepoTool] = {}


def register_tool(tool: RepoTool) -> None:
    """Add a tool to the global registry (project tools override framework)."""
    _TOOL_REGISTRY[tool.name] = tool


def get_tool(name: str) -> RepoTool | None:
    """Look up a registered tool by name."""
    return _TOOL_REGISTRY.get(name)


def registered_tool_deps() -> list[str]:
    """Collect, deduplicate, and sort deps from all registered tools."""
    seen: set[str] = set()
    for tool in _TOOL_REGISTRY.values():
        seen.update(tool.deps)
    return sorted(seen)


def invoke_tool(
    name: str,
    tokens: dict[str, str],
    config: dict[str, Any],
    dimensions: dict[str, str] | None = None,
    extra_args: dict[str, Any] | None = None,
) -> None:
    """Invoke a registered tool programmatically (e.g. prebuild/postbuild steps)."""
    tool = get_tool(name)
    if tool is None:
        raise KeyError(f"Tool '{name}' is not registered.")

    tool_config = config.get(name, {})
    if not isinstance(tool_config, dict):
        tool_config = {}

    ctx = ToolContext(
        workspace_root=Path(tokens.get("workspace_root", ".")),
        tokens=tokens,
        config=config,
        tool_config=tool_config,
        dimensions=dimensions or {},
        passthrough_args=[],
    )

    args: dict[str, Any] = {**tool.default_args(tokens)}
    args.update(tool_config)
    if extra_args:
        args.update(extra_args)

    tool.execute(ctx, args)


# ── Platform Detection ───────────────────────────────────────────────


def detect_platform_identifier(
    platform_override: str | None = None,
    conan_profile_path: Path | None = None,
) -> str:
    """Detect platform identifier for build directory structure.

    Priority: 1. Explicit override  2. Conan profile  3. Host auto-detect
    """
    if platform_override:
        return platform_override

    if conan_profile_path and conan_profile_path.exists():
        try:
            profile_content = conan_profile_path.read_text()
            os_match = re.search(r"^os=(\w+)", profile_content, re.MULTILINE)
            arch_match = re.search(r"^arch=(\w+)", profile_content, re.MULTILINE)
            if os_match and arch_match:
                return _map_platform_identifier(os_match.group(1), arch_match.group(1))
        except (OSError, UnicodeDecodeError):
            pass

    system = platform.system()
    machine = platform.machine().lower()

    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("arm64", "aarch64", "armv8"):
        arch = "arm64"
    else:
        arch = machine

    if system == "Windows":
        return f"windows-{arch}"
    elif system == "Linux":
        return f"linux-{arch}"
    elif system == "Darwin":
        return f"macos-{arch}"
    else:
        return f"{system.lower()}-{arch}"


def _map_platform_identifier(os_val: str, arch_val: str) -> str:
    """Map Conan os/arch settings to platform identifier."""
    if os_val == "Emscripten" and arch_val == "wasm":
        return "emscripten"

    os_map = {
        "Windows": "windows",
        "Linux": "linux",
        "Macos": "macos",
        "Darwin": "macos",
    }
    os_normalized = os_map.get(os_val, os_val.lower())

    arch_map = {
        "x86_64": "x64",
        "x86": "x86",
        "armv8": "arm64",
        "armv8_32": "arm",
        "wasm": "wasm",
    }
    arch_normalized = arch_map.get(arch_val, arch_val.lower())

    return f"{os_normalized}-{arch_normalized}"



# ── Process Execution ────────────────────────────────────────────────


def is_windows() -> bool:
    return platform.system() == "Windows"


def _is_ci() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


@contextlib.contextmanager
def log_section(title: str) -> Generator[None, None, None]:
    """Foldable CI section or styled terminal header."""
    if _is_ci():
        print(f"::group::{title}", flush=True)
    else:
        logger.info(f"── {title} ──")
    try:
        yield
    finally:
        if _is_ci():
            print("::endgroup::", flush=True)


def print_subprocess_line(line: str) -> None:
    text = line.rstrip()
    print(f"{Style.DIM}{text}{Style.RESET_ALL}")


@functools.cache
def find_venv_executable(name: str) -> str:
    """Find an executable in the virtual environment, fallback to system PATH."""
    python_exe = Path(sys.executable)
    scripts_dir = python_exe.parent
    exe_path = scripts_dir / (name + (".exe" if sys.platform == "win32" else ""))

    if exe_path.exists():
        return str(exe_path)

    exe_path_str = shutil.which(name)
    if exe_path_str:
        return exe_path_str

    logger.warning(f"Executable {name} not found in virtual environment or system PATH")
    return name


def sanitized_subprocess_env() -> dict[str, str]:
    """Return env overrides that strip repo-tool Python contamination.

    The generated shim (``repo`` / ``repo.cmd``) prepends the venv's Scripts
    directory to ``PATH`` and sets ``PYTHONPATH`` so the CLI can import
    ``repo_tools``.  These variables must **not** leak into build-tool
    subprocesses (Conan, CMake, …) because they can cause the wrong Python
    stdlib to be loaded — for example, a system Python 3.12 picking up the
    venv's Python 3.14 stdlib, resulting in ``SRE module mismatch`` or
    ``_thread`` attribute errors.

    Returns a dict suitable for the *env* parameter of :func:`run_command`
    or :class:`CommandGroup`.  The dict is merged **on top of**
    ``os.environ``, so only the keys that need overriding are present.
    """
    env: dict[str, str] = {}

    # Strip PYTHONPATH — only needed for repo_tools imports
    env["PYTHONPATH"] = ""

    # Strip PYTHONHOME if present
    if "PYTHONHOME" in os.environ:
        env["PYTHONHOME"] = ""

    # Remove venv Scripts from PATH so build tools find the system Python
    venv_bin = os.path.normcase(os.path.normpath(str(Path(sys.executable).parent)))
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    clean_parts = [
        p for p in path_parts
        if os.path.normcase(os.path.normpath(p)) != venv_bin
    ]
    env["PATH"] = os.pathsep.join(clean_parts)

    return env


def run_command(
    cmd: list[str],
    log_file: Path | None = None,
    env_script: Path | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Run a command and optionally tee output to a log file.

    If *env_script* is provided, the command is executed inside a shell
    that sources the script first.  Errors out when the script doesn't exist.
    """
    use_shell = False
    run_cmd: list[str] | str = cmd
    if env_script is not None:
        script = env_script
        if not script.suffix:
            script = script.with_suffix(".bat" if is_windows() else ".sh")
        if not script.exists():
            logger.error(f"env_script not found: {script}")
            sys.exit(1)
        if is_windows():
            cmd_str = subprocess.list2cmdline(cmd)
            run_cmd = f'call "{script}" >nul 2>&1 && {cmd_str}'
        else:
            cmd_str = shlex.join(cmd)
            run_cmd = f'. "{script}" >/dev/null 2>&1 && {cmd_str}'
        use_shell = True

    proc_env = {**os.environ, **env} if env else None

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "w", encoding="utf-8", errors="replace") as f:
            process = subprocess.Popen(
                run_cmd,
                shell=use_shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=cwd,
                env=proc_env,
            )
            for line in process.stdout:
                print_subprocess_line(line)
                f.write(line)
            process.wait()
            if process.returncode != 0:
                sys.exit(process.returncode)
    else:
        try:
            subprocess.run(run_cmd, shell=use_shell, check=True, cwd=cwd, env=proc_env)
        except subprocess.CalledProcessError as e:
            sys.exit(e.returncode)


def remove_tree_with_retries(
    path: Path, attempts: int = 5, delay: float = 1.0,
) -> None:
    """Remove a directory tree with retry logic for locked files (Windows)."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if attempt < attempts - 1:
                logger.warning(
                    f"Permission denied removing {path}, "
                    f"retrying in {delay}s ({attempt + 1}/{attempts})"
                )
                time.sleep(delay)
            else:
                raise


def resolve_path(root: Path, template: str, tokens: dict[str, str]) -> Path:
    """Resolve a path template using tokens."""
    formatter = TokenFormatter(tokens)
    resolved = formatter.resolve(template)
    path = Path(resolved)
    if not path.is_absolute():
        path = root / path
    return path


# ── Path Utilities ───────────────────────────────────────────────────


def glob_paths(pattern: Path | str) -> list[Path]:
    """Expand a glob pattern to a sorted list of matching file paths.

    Returns a single-element list for non-glob paths.
    """
    pattern_text = str(pattern)
    if any(char in pattern_text for char in ("*", "?", "[")):
        return sorted(Path(match) for match in glob.glob(pattern_text, recursive=True))
    return [Path(pattern_text)]


# ── Command Group ────────────────────────────────────────────────────


class CommandGroup:
    """A labeled unit of work that runs commands and reports results.

    Usage::

        with CommandGroup("Building") as g:
            g.run(["cmake", "--build", "build"])
            g.run(["cmake", "--install", "build"])

    Features:
    - Labels each phase with a clear header
    - Tracks pass/fail per group
    - Dimmed subprocess output, summary on completion
    - Optional per-group log file
    - CI fold markers (``::group::``) in GitHub Actions
    """

    def __init__(
        self,
        label: str,
        log_file: Path | None = None,
        env_script: Path | None = None,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.label = label
        self.log_file = log_file
        self.env_script = env_script
        self.cwd = cwd
        self.env = env
        self._commands_run = 0
        self._failed = False

    def __enter__(self) -> CommandGroup:
        if _is_ci():
            print(f"::group::{self.label}", flush=True)
        else:
            logger.info(f"── {self.label} ──")
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if _is_ci():
            print("::endgroup::", flush=True)
        if exc_type is not None:
            return  # let the exception propagate
        if self._failed:
            logger.error(f"  ✗ {self.label} failed")
        else:
            logger.info(f"  ✓ {self.label} ({self._commands_run} command(s))")

    def run(
        self,
        cmd: list[str],
        log_file: Path | None = None,
        env_script: Path | None = None,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Run a command within this group.

        Per-call *log_file*, *env_script*, *cwd*, and *env* override the
        group defaults.  Per-call *env* is merged on top of group-level env.
        """
        lf = log_file or self.log_file
        es = env_script or self.env_script
        cw = cwd or self.cwd
        merged_env = {**(self.env or {}), **(env or {})} or None
        try:
            run_command(cmd, log_file=lf, env_script=es, cwd=cw, env=merged_env)
            self._commands_run += 1
        except SystemExit:
            self._failed = True
            raise


# ── Normalization Helpers ────────────────────────────────────────────


def to_cmake_build_type(value: str | None) -> str:
    if not value:
        return "Debug"
    mapping = {
        "debug": "Debug",
        "release": "Release",
        "relwithdebinfo": "RelWithDebInfo",
        "minsizerel": "MinSizeRel",
    }
    return mapping.get(str(value).casefold(), str(value))




