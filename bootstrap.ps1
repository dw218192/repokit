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

# ── Install dependencies ─────────────────────────────────────────────

& $Uv pip install --python $Py -r "$FrameworkDir\requirements.txt"

$ProjectReqs = "$Root\tools\requirements.txt"
if (Test-Path $ProjectReqs) {
    & $Uv pip install --python $Py -r $ProjectReqs
}

# ── Generate ./repo shim ─────────────────────────────────────────────

# Convert Windows paths to Git Bash compatible paths for the shim
$FrameworkDirBash = $FrameworkDir -replace '\\','/'
$PyBash = $Py -replace '\\','/'
$RootBash = $Root -replace '\\','/'

$ShimPath = "$Root\repo"
$ShimContent = @"
#!/bin/bash
PYTHONPATH="$FrameworkDirBash`${PYTHONPATH:+:`$PYTHONPATH}" exec "$PyBash" -m repo_tools.cli --workspace-root "$RootBash" "`$@"
"@
[System.IO.File]::WriteAllText($ShimPath, $ShimContent.Replace("`r`n", "`n"))

Write-Host "OK: $Venv"
Write-Host "Run ./repo --help to get started."
