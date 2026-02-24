#!/bin/bash
set -e

# Repokit bootstrap: venv + uv + install deps + generate ./repo shim.
# Usage: bootstrap.sh [--clean] [workspace_root]

FRAMEWORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Parse args ────────────────────────────────────────────────────────

CLEAN=false
ROOT=""
for arg in "$@"; do
    if [[ "$arg" == "--clean" ]]; then
        CLEAN=true
    else
        ROOT="$arg"
    fi
done

# ── Derive project root (mirrors _bootstrap.derive_project_root) ────

if [[ -n "$ROOT" ]]; then
    ROOT="$(cd "$ROOT" && pwd)"
    echo "Using explicit project root: $ROOT"
else
    GIT_ROOT="$(git -C "$FRAMEWORK_DIR" rev-parse --show-toplevel 2>/dev/null)" || true
    if [[ -z "$GIT_ROOT" ]]; then
        echo "ERROR: not a git repository — pass the project root explicitly:" >&2
        echo "  $0 /path/to/project" >&2
        exit 1
    fi
    if [[ "$GIT_ROOT" == "$FRAMEWORK_DIR" ]]; then
        # Inside the submodule's own repo — need the parent.
        ROOT="$(git -C "$FRAMEWORK_DIR" rev-parse --show-superproject-working-tree 2>/dev/null)" || true
        if [[ -z "$ROOT" ]]; then
            echo "ERROR: could not determine project root from submodule — pass it explicitly:" >&2
            echo "  $0 /path/to/project" >&2
            exit 1
        fi
    else
        ROOT="$GIT_ROOT"
    fi
    echo "Derived project root: $ROOT"
fi

TOOLS="$ROOT/_tools"
BIN="$TOOLS/bin"
PYS="$TOOLS/python"
CACHE="$TOOLS/cache/uv"
VENV="$TOOLS/venv"

# ── Clean ─────────────────────────────────────────────────────────────

if $CLEAN; then
    echo "Cleaning bootstrap artifacts..."
    rm -rf "$TOOLS"
    rm -f "$ROOT/tools/pyproject.toml" "$ROOT/tools/uv.lock"
    rm -f "$ROOT/repo" "$ROOT/repo.cmd"
fi

mkdir -p "$BIN" "$PYS" "$CACHE"

# ── uv ───────────────────────────────────────────────────────────────

UV="$BIN/uv"
if [[ ! -f "$UV" ]]; then
    export UV_INSTALL_DIR="$BIN"
    export UV_NO_MODIFY_PATH="1"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

export UV_CACHE_DIR="$CACHE"
export UV_PYTHON_INSTALL_DIR="$PYS"
export UV_MANAGED_PYTHON="1"

# ── Python venv ──────────────────────────────────────────────────────

# Download Python if not already present
if ! "$UV" python install --no-bin; then
    # Install failed — verify Python is still usable (e.g. already installed)
    if ! "$UV" python find >/dev/null 2>&1; then
        echo "ERROR: uv python install failed and no usable Python found"
        exit 1
    fi
fi
if [[ ! -d "$VENV" ]]; then
    "$UV" venv "$VENV"
fi

# Detect python path
if [[ -f "$VENV/Scripts/python.exe" ]]; then
    PY="$VENV/Scripts/python.exe"
elif [[ -f "$VENV/bin/python" ]]; then
    PY="$VENV/bin/python"
else
    echo "ERROR: Could not find Python in venv"
    exit 1
fi

# ── Bootstrap: deps + shims ───────────────────────────────────────────

# Stdlib-only — no pip install needed before this.
PYTHONPATH="$FRAMEWORK_DIR" "$PY" -m repo_tools._bootstrap "$FRAMEWORK_DIR" "$ROOT" "$UV"

echo "OK: $VENV"
