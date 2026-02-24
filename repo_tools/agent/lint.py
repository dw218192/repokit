"""Shared lint helpers used by the stdio MCP server.

No third-party imports — the lightweight stdio server can import
without pulling in the full package.  ``find_executable`` is
stdlib-only and imported from ``repo_tools.features``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..features import find_executable as _find_executable

# ── Constants ─────────────────────────────────────────────────────────────────

_PY_EXTENSIONS = frozenset({".py", ".pyi"})
_CPP_EXTENSIONS = frozenset({".cpp", ".h", ".hpp", ".c", ".cc", ".cxx", ".hxx"})

# Sensible default rule set: pyflakes + targeted security + bugbear + simplify.
# Excludes E (formatting — use the format tool) and broad S (too noisy for
# test files and subprocess-using code).  Users can override via config.yaml
# `agent.ruff_select`.
_DEFAULT_SELECT = "F,S110,S301,S307,S602,B,SIM"


def _find_compile_commands(start: Path) -> str | None:
    """Search upward from *start* for a compile_commands.json file.

    Returns the containing directory as a string, or None.
    """
    current = start if start.is_dir() else start.parent
    for parent in [current, *current.parents]:
        if (parent / "compile_commands.json").exists():
            return str(parent)
        # Also check common build subdirectories
        for build_dir in ("build", "out", "cmake-build-debug"):
            candidate = parent / build_dir / "compile_commands.json"
            if candidate.exists():
                return str(parent / build_dir)
    return None


# ── Ruff ─────────────────────────────────────────────────────────────────────


def _call_ruff_check(
    path: str,
    *,
    default_select: str | None = None,
    default_ignore: str | None = None,
) -> dict[str, Any]:
    """Run ``ruff check`` and return a result dict."""
    select = (default_select or "").strip() or _DEFAULT_SELECT
    ignore = (default_ignore or "").strip() or None

    exe = _find_executable("ruff")
    if exe is None:
        return {"isError": True, "text": "ruff is not installed."}

    cmd = [exe, "check", "--output-format=concise"]
    if select:
        cmd.extend(["--select", select])
    if ignore:
        cmd.extend(["--ignore", ignore])
    cmd.append(path)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"isError": True, "text": "ruff check timed out."}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"isError": True, "text": f"ruff check failed: {exc}"}

    output = (proc.stdout or "") + (proc.stderr or "")
    return {"text": output.strip() or "No issues found by ruff."}


# ── Clang-tidy ───────────────────────────────────────────────────────────────


def _call_clang_tidy(path: str) -> dict[str, Any]:
    """Run ``clang-tidy`` on C/C++ files and return a result dict.

    Automatically searches upward for compile_commands.json.
    """
    exe = _find_executable("clang-tidy")
    if exe is None:
        return {"isError": True, "text": "clang-tidy is not installed."}

    target = Path(path)
    if target.is_file():
        files = [str(target)]
    elif target.is_dir():
        files = [
            str(f) for f in target.rglob("*")
            if f.suffix in _CPP_EXTENSIONS
        ]
    else:
        return {"isError": True, "text": f"Path does not exist: {path!r}"}

    if not files:
        return {"text": "No C/C++ files found."}

    cmd = [exe]
    compile_dir = _find_compile_commands(target)
    if compile_dir:
        cmd.extend(["-p", compile_dir])
    cmd.extend(files)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"isError": True, "text": "clang-tidy timed out."}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"isError": True, "text": f"clang-tidy failed: {exc}"}

    output = (proc.stdout or "") + (proc.stderr or "")
    return {"text": output.strip() or "No issues found by clang-tidy."}


# ── Unified entry point ─────────────────────────────────────────────────────


def _detect_languages(path: Path) -> tuple[bool, bool]:
    """Return (has_python, has_cpp) for a path."""
    if path.is_file():
        return path.suffix in _PY_EXTENSIONS, path.suffix in _CPP_EXTENSIONS
    if path.is_dir():
        has_py = has_cpp = False
        for f in path.rglob("*"):
            if f.suffix in _PY_EXTENSIONS:
                has_py = True
            elif f.suffix in _CPP_EXTENSIONS:
                has_cpp = True
            if has_py and has_cpp:
                break
        return has_py, has_cpp
    return False, False


def call_lint(
    args: dict[str, Any],
    *,
    default_select: str | None = None,
    default_ignore: str | None = None,
) -> dict[str, Any]:
    """Run the appropriate linter(s) based on file type."""
    path_str = (args.get("path") or "").strip() or "."
    target = Path(path_str)

    if not target.exists():
        return {
            "isError": True,
            "text": f"Path does not exist: {path_str!r}",
        }

    has_py, has_cpp = _detect_languages(target)

    if not has_py and not has_cpp:
        return {"text": "No lintable files found (Python or C/C++)."}

    sections: list[str] = []

    if has_py:
        result = _call_ruff_check(
            path_str,
            default_select=default_select,
            default_ignore=default_ignore,
        )
        if result.get("isError"):
            sections.append(f"[ruff] {result['text']}")
        else:
            sections.append(result["text"])

    if has_cpp:
        result = _call_clang_tidy(path_str)
        if result.get("isError"):
            sections.append(f"[clang-tidy] {result['text']}")
        else:
            sections.append(result["text"])

    return {"text": "\n\n".join(sections)}
