$ErrorActionPreference = "Stop"

# Repokit bootstrap: venv + uv + install deps + generate ./repo shim.
# Run from any project that submodules repokit at tools/framework/.

$FrameworkDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Root = (Resolve-Path "$FrameworkDir\..\..").Path
$Tools = "$Root\_tools"
$Bin = "$Tools\bin"
$Pys = "$Tools\python"
$Cache = "$Tools\cache\uv"
$Venv = "$Tools\venv"

New-Item -ItemType Directory -Force -Path $Bin, $Pys, $Cache | Out-Null

# ── uv ───────────────────────────────────────────────────────────────

$Uv = "$Bin\uv.exe"
if (-not (Test-Path $Uv)) {
    $env:UV_INSTALL_DIR = $Bin
    $env:UV_NO_MODIFY_PATH = "1"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}

$env:UV_CACHE_DIR = $Cache
$env:UV_PYTHON_INSTALL_DIR = $Pys
$env:UV_MANAGED_PYTHON = "1"

# ── Python venv ──────────────────────────────────────────────────────

& $Uv python install
if (-not (Test-Path $Venv)) {
    & $Uv venv $Venv
}

$Py = "$Venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    $Py = "$Venv\bin\python"
}

# ── Bootstrap: deps + shims ───────────────────────────────────────────

# Stdlib-only — no pip install needed before this.
$env:PYTHONPATH = $FrameworkDir
& $Py -m repo_tools._bootstrap $FrameworkDir $Root $Uv

Write-Host "OK: $Venv"
