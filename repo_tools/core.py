"""Core framework: RepoTool base, token system, config @filter resolver, utilities."""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import logging
import os
import platform
import re
import shutil
import string
import subprocess
import sys
import time
from collections.abc import Generator
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
        seen: set[str] = set()
        result = template
        for _ in range(self.MAX_DEPTH):
            try:
                expanded = result.format_map(self._tokens)
            except KeyError as exc:
                missing = exc.args[0] if exc.args else "unknown"
                raise KeyError(f"Missing token: {missing}") from exc
            if expanded == result:
                return expanded
            key = expanded
            if key in seen:
                raise ValueError(f"Circular token reference: {key}")
            seen.add(key)
            result = expanded
        raise ValueError(f"Token expansion exceeded {self.MAX_DEPTH} iterations")


def _fwd(p: str) -> str:
    """Normalize path to forward slashes (safe for shlex.split on Windows)."""
    return Path(p).as_posix()


# Built-in tokens resolved from the runtime environment.
def _builtin_tokens() -> dict[str, str]:
    system = platform.system()
    is_win = system == "Windows"
    is_mac = system == "Darwin"
    return {
        "exe_ext": ".exe" if is_win else "",
        "shell_ext": ".cmd" if is_win else ".sh",
        "lib_ext": ".dll" if is_win else (".dylib" if is_mac else ".so"),
        "path_sep": ";" if is_win else ":",
        "repo": f'"{_fwd(sys.executable)}" -m repo_tools.cli --workspace-root "{{workspace_root}}"',
    }


def resolve_tokens(
    workspace_root: str,
    config: dict[str, Any],
    dimension_values: dict[str, str],
) -> dict[str, str]:
    """Build the full token dictionary.

    Merge order (later wins):
      1. Built-in tokens (exe_ext, shell_ext, etc.)
      2. Variable tokens from config
      3. Computed paths (workspace_root, build_root, etc.)
      4. Dimension values (platform, build_type, etc.)
    """
    tokens: dict[str, str] = _builtin_tokens()

    # Variable tokens from config
    for key, value in config.get("tokens", {}).items():
        if isinstance(value, list):
            continue  # dimension tokens handled elsewhere
        tokens[key] = str(value)

    # Core path tokens
    build_root = config.get("tokens", {}).get("build_root") or \
                 _get_config_value(config, "repo_paths.build_root",
                 _get_config_value(config, "paths.build_root", "_build"))
    logs_root = config.get("tokens", {}).get("logs_root") or \
                _get_config_value(config, "repo_paths.logs_root",
                _get_config_value(config, "paths.logs_root", "_logs"))

    tokens["workspace_root"] = _fwd(workspace_root)
    tokens["build_root"] = _fwd(str(Path(workspace_root) / build_root))
    tokens["logs_root"] = _fwd(str(Path(workspace_root) / logs_root))

    # Dimension values override
    tokens.update(dimension_values)

    # Resolve any cross-references in variable tokens
    formatter = TokenFormatter(tokens)
    resolved: dict[str, str] = {}
    for key, value in tokens.items():
        if "{" in str(value):
            try:
                resolved[key] = formatter.resolve(str(value))
            except (KeyError, ValueError):
                resolved[key] = str(value)
        else:
            resolved[key] = str(value)

    # Computed compound tokens
    resolved["build_dir"] = _fwd(str(
        Path(resolved["build_root"]) / resolved.get("platform", "default") / resolved.get("build_type", "Debug")
    ))

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


def _get_config_value(config: dict, key_path: str, default: str = "") -> str:
    """Nested dict lookup by dot-separated key path."""
    current: Any = config
    for key in key_path.split("."):
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return str(current) if current is not None else default



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

    def setup(self, cmd: click.Command) -> click.Command:
        """Add click options/arguments to the command. Return the command."""
        return cmd

    def default_args(self, tokens: dict[str, str]) -> dict[str, Any]:
        """Return default args dict before config/CLI merge."""
        return {}

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
        except Exception:
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


def run_command(
    cmd: list[str],
    log_file: Path | None = None,
    env_script: Path | None = None,
) -> None:
    """Run a command and optionally tee output to a log file.

    If *env_script* is provided and exists, the command is executed
    inside a shell that sources the script first.
    """
    use_shell = False
    run_cmd: list[str] | str = cmd
    if env_script is not None:
        script = env_script
        if not script.suffix:
            script = script.with_suffix(".bat" if is_windows() else ".sh")
        if script.exists():
            cmd_str = subprocess.list2cmdline(cmd)
            if is_windows():
                run_cmd = f'call "{script}" >nul 2>&1 && {cmd_str}'
            else:
                run_cmd = f'source "{script}" >/dev/null 2>&1 && {cmd_str}'
            use_shell = True

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
            )
            for line in process.stdout:
                print_subprocess_line(line)
                f.write(line)
            process.wait()
            if process.returncode != 0:
                sys.exit(process.returncode)
    else:
        try:
            subprocess.run(run_cmd, shell=use_shell, check=True)
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


# ── Normalization Helpers ────────────────────────────────────────────


def normalize_build_type(value: str | None) -> str:
    if not value:
        return "Debug"
    mapping = {
        "debug": "Debug",
        "release": "Release",
        "relwithdebinfo": "RelWithDebInfo",
        "minsizerel": "MinSizeRel",
    }
    return mapping.get(str(value).casefold(), str(value))




