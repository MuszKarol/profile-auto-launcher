<#
.SYNOPSIS
  Installs profile-auto-launcher for the current user and registers it
  to autostart at sign-in via a shortcut in the Startup folder.

.PARAMETER NoAutostart
  Install the package but do not create the Startup-folder shortcut.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -NoAutostart
#>
[CmdletBinding()]
param(
    [switch]$NoAutostart
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoDir   = Split-Path -Parent $scriptDir

Write-Host "==> Installing profile-auto-launcher from $repoDir"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    throw "Python not found on PATH. Install Python 3.10+ from https://python.org (check 'Add to PATH'), then re-run."
}

$pyVersion = & $python.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
$pyOk      = & $python.Source -c "import sys; print(1 if sys.version_info >= (3, 10) else 0)"
if ($pyOk -ne '1') {
    throw "Python $pyVersion is too old; need 3.10+."
}
Write-Host "==> Using Python $pyVersion ($($python.Source))"

& $python.Source -m pip install --user --upgrade "$repoDir[full]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit $LASTEXITCODE" }

# Locate palaunch.exe — prefer PATH, fall back to the user Scripts dir.
$palaunch = Get-Command palaunch -ErrorAction SilentlyContinue
if ($palaunch) {
    $palaunchPath = $palaunch.Source
} else {
    $userBase  = & $python.Source -c "import site; print(site.USER_BASE)"
    $candidate = Join-Path $userBase "Scripts\palaunch.exe"
    if (Test-Path $candidate) {
        $palaunchPath = $candidate
        Write-Warning "palaunch not on PATH. Add '$(Split-Path $candidate -Parent)' to your PATH."
    } else {
        throw "palaunch.exe not found after install. Expected at $candidate."
    }
}
Write-Host "==> palaunch binary: $palaunchPath"

# Copy sample profiles into %APPDATA%\profile-auto-launcher\profiles (skip if already present).
$profilesDir = Join-Path $env:APPDATA "profile-auto-launcher\profiles"
New-Item -ItemType Directory -Force -Path $profilesDir | Out-Null
Get-ChildItem -Path (Join-Path $repoDir "profiles") -Filter "*.yaml" | ForEach-Object {
    $dest = Join-Path $profilesDir $_.Name
    if (-not (Test-Path $dest)) {
        Copy-Item $_.FullName $dest
        Write-Host "    installed profile: $($_.Name)"
    }
}
Write-Host "==> Profiles dir:    $profilesDir"

if ($NoAutostart) {
    Write-Host "==> Autostart skipped (-NoAutostart)."
    Write-Host ""
    Write-Host "Done. Start manually with:  palaunch tray"
    return
}

# Create a Startup-folder shortcut that runs `palaunch tray` minimized.
$startup      = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup "Profile Auto Launcher.lnk"
$shell        = New-Object -ComObject WScript.Shell
$shortcut     = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath       = $palaunchPath
$shortcut.Arguments        = "tray"
$shortcut.WorkingDirectory = (Split-Path $palaunchPath -Parent)
$shortcut.WindowStyle      = 7        # Minimized
$shortcut.Description      = "Profile Auto Launcher (tray)"
$shortcut.Save()

Write-Host "==> Autostart shortcut: $shortcutPath"
Write-Host ""
Write-Host "Installation complete. Launcher will start at your next sign-in."
Write-Host "To start it now, run:  palaunch tray"
