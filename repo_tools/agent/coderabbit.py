"""Shared CodeRabbit CLI helpers used by both the stdio and HTTP MCP servers.

Stdlib-only — no third-party imports so the lightweight stdio server
(``coderabbit_mcp.py``) can import without pulling in the full package.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── Constants ─────────────────────────────────────────────────────────────────

NOT_INSTALLED = (
    "coderabbit CLI is not installed — fall back to manual review.\n"
    "Linux/macOS: curl -fsSL https://cli.coderabbit.ai/install.sh | sh\n"
    "Windows: wsl -- curl -fsSL https://cli.coderabbit.ai/install.sh | sh"
)
NOT_AUTHED = (
    "coderabbit is not authenticated — fall back to manual review.\n"
    "Authenticate with: coderabbit auth login"
)

VALID_REVIEW_TYPES = frozenset({"committed", "uncommitted", "all"})


# ── Platform helpers ──────────────────────────────────────────────────────────


def is_windows() -> bool:
    return sys.platform == "win32"


def check_installed() -> bool:
    """Return True if coderabbit is available (natively or via WSL on Windows)."""
    if is_windows():
        try:
            result = subprocess.run(
                ["wsl", "bash", "-lc", "command -v coderabbit"],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=10,
            )
            return result.returncode == 0
        except OSError:
            return False
        except subprocess.SubprocessError as exc:
            print(f"coderabbit: WSL check failed: {exc}", file=sys.stderr)
            return False
    return shutil.which("coderabbit") is not None


def coderabbit_cmd(*args: str) -> list[str]:
    """Return a command list to invoke coderabbit with *args*.

    On Windows the CLI lives inside WSL and is typically installed as a
    shell alias/function, so we must run it through a login shell.
    """
    if is_windows():
        return ["wsl", "bash", "-lc", shlex.join(["coderabbit", *args])]
    return ["coderabbit", *args]


# ── Core review logic ─────────────────────────────────────────────────────────


TOOL_SCHEMA: dict = {
    "name": "coderabbit_review",
    "description": (
        "Run the CodeRabbit CLI to review code changes in a git worktree. "
        "Returns plain-text reviewer feedback. "
        "If the CLI is not installed or not authenticated, returns an error message "
        "instructing you to fall back to manual review."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "worktree_path": {
                "type": "string",
                "description": (
                    "Path to the git worktree whose changes should be reviewed. "
                    "Defaults to '.' (the current working directory)."
                ),
                "default": ".",
            },
            "type": {
                "type": "string",
                "enum": ["committed", "uncommitted", "all"],
                "default": "committed",
                "description": "Which changes to review: 'committed' (default), 'uncommitted', or 'all'.",
            },
        },
        "required": [],
    },
}


def call_review(args: dict[str, Any], *, logger: Any = None) -> dict[str, Any]:
    """Run ``coderabbit review --plain`` and return a result dict.

    Parameters
    ----------
    args:
        Tool arguments (``worktree_path``, ``type``).
    logger:
        Optional logger for info-level messages (used by the HTTP MCP server).
    """
    worktree_path = (args.get("worktree_path") or "").strip() or "."
    review_type = args.get("type") or "committed"

    if review_type not in VALID_REVIEW_TYPES:
        return {
            "isError": True,
            "text": f"Invalid review type: {review_type!r} (expected one of {', '.join(sorted(VALID_REVIEW_TYPES))})",
        }

    if not Path(worktree_path).is_dir():
        return {"isError": True, "text": f"worktree_path is not a directory: {worktree_path!r}"}

    if not check_installed():
        return {"isError": True, "text": NOT_INSTALLED}

    try:
        auth = subprocess.run(
            coderabbit_cmd("auth", "status"),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "isError": True,
            "text": f"coderabbit auth check failed: {exc} — fall back to manual review",
        }

    if auth.returncode != 0:
        return {"isError": True, "text": NOT_AUTHED}

    try:
        proc = subprocess.run(
            coderabbit_cmd("review", "--plain", "--type", review_type),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            cwd=worktree_path,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"isError": True, "text": "coderabbit review timed out — fall back to manual review"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"isError": True, "text": f"coderabbit review failed: {exc} — fall back to manual review"}

    output = (proc.stdout or "") + (proc.stderr or "")

    if logger is not None:
        logger.info("coderabbit_review → %r (%s): %d chars", worktree_path, review_type, len(output))

    return {"text": output.strip() or "No issues found by CodeRabbit."}
