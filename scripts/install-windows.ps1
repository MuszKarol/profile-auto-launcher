<#
.SYNOPSIS
  Installs profile-auto-launcher for the current user.

.DESCRIPTION
  Installs the package with pip, then hands the rest — sample profiles, the
  JSON Schema, the Start Menu shortcut and the sign-in entry — to
  `palaunch install`, so this script and the setup window cannot drift apart.

.PARAMETER NoAutostart
  Install without registering the launcher to start at sign-in.

.PARAMETER Gui
  Open the setup window instead of installing on the command line.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoAutostart,
    [switch]$Gui
)

$ErrorActionPreference = 'Stop'
$repoDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "==> Installing profile-auto-launcher from $repoDir"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) {
    throw "Python not found on PATH. Install Python 3.10+ from https://python.org (tick 'Add to PATH'), then re-run."
}

$pyVersion = & $python.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
$pyOk      = & $python.Source -c "import sys; print(1 if sys.version_info >= (3, 10) else 0)"
if ($pyOk -ne '1') { throw "Python $pyVersion is too old; need 3.10+." }
Write-Host "==> Using Python $pyVersion ($($python.Source))"

$null = & $python.Source -m pip --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> pip not found - installing via ensurepip..."
    & $python.Source -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw "ensurepip failed." }
}

& $python.Source -m pip install --user --upgrade "$repoDir[full]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit $LASTEXITCODE" }

# Prefer the Scripts directory we just installed into: an older copy elsewhere
# on PATH would otherwise shadow the fresh install.
$userScripts = & $python.Source -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"
$candidate = Join-Path $userScripts "palaunch.exe"
if (Test-Path $candidate) {
    $palaunchPath = $candidate
} else {
    $found = Get-Command palaunch -ErrorAction SilentlyContinue
    if (-not $found) { throw "palaunch.exe not found after install. Expected at $candidate." }
    $palaunchPath = $found.Source
}
Write-Host "==> palaunch binary: $palaunchPath"

$currentPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
if ($currentPath -notlike "*$userScripts*") {
    [Environment]::SetEnvironmentVariable('PATH', "$currentPath;$userScripts", 'User')
    Write-Host "==> Added '$userScripts' to your user PATH (restart your terminal)."
}

if ($Gui) {
    & $palaunchPath install
    exit 0
}

if ($NoAutostart) {
    & $palaunchPath install --cli --no-autostart
} else {
    & $palaunchPath install --cli
}

Write-Host ""
Write-Host "Done. Open the launcher with:  palaunch pick"
