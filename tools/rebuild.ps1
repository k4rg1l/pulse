<#
.SYNOPSIS
    Cleanly kill any running Pulse instance and rebuild dist\Pulse.exe from scratch.

.DESCRIPTION
    1. Stops any running Pulse — both the built Pulse.exe AND a dev `python main.py`.
       (Only python processes whose command line contains main.py are touched, so
       unrelated Python — Claude Code's MCP backends, etc. — are left alone.)
    2. Deletes build\ and dist\ so the build is truly clean.
    3. Runs PyInstaller against pulse.spec.
    4. Reports the resulting exe (and optionally launches it).

.PARAMETER Run
    Launch the freshly built dist\Pulse.exe when the build succeeds.

.EXAMPLE
    .\tools\rebuild.ps1
    .\tools\rebuild.ps1 -Run
#>
param([switch]$Run)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

Write-Host '==> Stopping any running Pulse...' -ForegroundColor Cyan
# The built exe (releases the lock on dist\Pulse.exe so we can overwrite it).
Get-Process -Name 'Pulse' -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "    kill Pulse.exe  PID $($_.Id)"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
# The dev run (python main.py). Narrow filter: never a blanket 'python*' kill.
Get-CimInstance Win32_Process -Filter "name = 'python.exe' OR name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*main.py*' } | ForEach-Object {
        Write-Host "    kill python   PID $($_.ProcessId)  ($($_.CommandLine))"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Milliseconds 600   # let file locks release

Write-Host '==> Cleaning build artifacts...' -ForegroundColor Cyan
foreach ($d in 'build', 'dist') {
    if (Test-Path $d) { Remove-Item $d -Recurse -Force }
}

Write-Host '==> Building (PyInstaller --clean)...' -ForegroundColor Cyan
python -m PyInstaller pulse.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host 'BUILD FAILED.' -ForegroundColor Red
    exit 1
}

$exe = Join-Path $repo 'dist\Pulse.exe'
if (-not (Test-Path $exe)) {
    Write-Host "Build reported success but $exe is missing." -ForegroundColor Red
    exit 1
}
$sizeMb = [Math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "==> Built  $exe  ($sizeMb MB)" -ForegroundColor Green

if ($Run) {
    Write-Host '==> Launching...' -ForegroundColor Cyan
    Start-Process $exe
}
