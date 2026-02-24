$ErrorActionPreference = "Stop"

# Repokit bootstrap: venv + uv + install deps + generate ./repo shim.
# Usage: bootstrap.ps1 [--clean] [workspace_root]

$FrameworkDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# ── Parse args ────────────────────────────────────────────────────────

$Clean = $false
$Root = $null
foreach ($a in $args) {
    if ($a -eq "--clean") { $Clean = $true }
    else { $Root = $a }
}

# ── Derive project root (mirrors _bootstrap.derive_project_root) ────

if ($Root) {
    $Root = (Resolve-Path $Root).Path
    Write-Host "Using explicit project root: $Root"
} else {
    $GitRoot = (git -C $FrameworkDir rev-parse --show-toplevel 2>$null)
    if (-not $GitRoot) {
        Write-Error "Not a git repository - pass the project root explicitly: $($MyInvocation.MyCommand.Definition) <path>"
        exit 1
    }
    $GitRoot = $GitRoot.Replace("/", "\")
    if ($GitRoot -eq $FrameworkDir) {
        # Inside the submodule's own repo - need the parent.
        $Root = (git -C $FrameworkDir rev-parse --show-superproject-working-tree 2>$null)
        if (-not $Root) {
            Write-Error "Could not determine project root from submodule - pass it explicitly: $($MyInvocation.MyCommand.Definition) <path>"
            exit 1
        }
        $Root = $Root.Replace("/", "\")
    } else {
        $Root = $GitRoot
    }
    Write-Host "Derived project root: $Root"
}

$Tools = "$Root\_tools"
$Bin = "$Tools\bin"
$Pys = "$Tools\python"
$Cache = "$Tools\cache\uv"
$Venv = "$Tools\venv"

# ── Clean ─────────────────────────────────────────────────────────────

if ($Clean) {
    Write-Host "Cleaning bootstrap artifacts..."
    if (Test-Path $Tools) { Remove-Item -Recurse -Force $Tools }
    Remove-Item -Force -ErrorAction SilentlyContinue "$Root\tools\pyproject.toml", "$Root\tools\uv.lock"
    Remove-Item -Force -ErrorAction SilentlyContinue "$Root\repo", "$Root\repo.cmd"
}

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
