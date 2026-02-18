#!/bin/bash
set -e

# Repokit bootstrap: venv + uv + install deps + generate ./repo shim.
# Run from any project that submodules repokit at tools/framework/.

FRAMEWORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$FRAMEWORK_DIR/../.." && pwd)"
TOOLS="$ROOT/_tools"
BIN="$TOOLS/bin"
PYS="$TOOLS/python"
CACHE="$TOOLS/cache/uv"
VENV="$TOOLS/venv"

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

"$UV" python install
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

# ── Install dependencies ─────────────────────────────────────────────

# Framework requirements
"$UV" pip install --python "$PY" -r "$FRAMEWORK_DIR/requirements.txt"

# Project requirements (if present)
PROJECT_REQS="$ROOT/tools/requirements.txt"
if [[ -f "$PROJECT_REQS" ]]; then
    "$UV" pip install --python "$PY" -r "$PROJECT_REQS"
fi

# ── Generate ./repo shim ─────────────────────────────────────────────

# The shim sets PYTHONPATH so that repo_tools is importable as a
# namespace package, then runs it as a module.  Project tool dirs are
# discovered at runtime by cli.py.

SHIM="$ROOT/repo"
cat > "$SHIM" <<'SHIMEOF'
#!/bin/bash
FRAMEWORK_DIR="PLACEHOLDER_FRAMEWORK"
PY="PLACEHOLDER_PY"
ROOT="PLACEHOLDER_ROOT"
PYTHONPATH="$FRAMEWORK_DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m repo_tools.cli --workspace-root "$ROOT" "$@"
SHIMEOF

# Replace placeholders with actual paths
sed -i "s|PLACEHOLDER_FRAMEWORK|$FRAMEWORK_DIR|g" "$SHIM"
sed -i "s|PLACEHOLDER_PY|$PY|g" "$SHIM"
sed -i "s|PLACEHOLDER_ROOT|$ROOT|g" "$SHIM"
chmod +x "$SHIM"

echo "OK: $VENV"
echo "Run ./repo --help to get started."
