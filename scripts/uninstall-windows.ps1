<#
.SYNOPSIS
  Removes the sign-in entry, the shortcuts and the package.
  Profiles and settings in the config directory are left alone.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

$palaunch = Get-Command palaunch -ErrorAction SilentlyContinue
if ($palaunch) {
    & $palaunch.Source install --cli --uninstall
}

# Older versions dropped a shortcut straight into the Startup folder.
$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
foreach ($name in @("Profile Auto Launcher.lnk", "profile-auto-launcher.lnk")) {
    $link = Join-Path $startup $name
    if (Test-Path $link) { Remove-Item $link -Force; Write-Host "removed $link" }
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if ($python) { & $python.Source -m pip uninstall -y profile-auto-launcher }

Write-Host ""
Write-Host "Uninstall complete. Profiles kept in your config directory."
