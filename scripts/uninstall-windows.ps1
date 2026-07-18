<#
.SYNOPSIS
  Removes the Startup-folder shortcut and uninstalls the package.
  Leaves user profiles in %APPDATA%\profile-auto-launcher\ untouched.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

$startup      = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup "Profile Auto Launcher.lnk"
if (Test-Path $shortcutPath) {
    Remove-Item -Force $shortcutPath
    Write-Host "Removed autostart shortcut: $shortcutPath"
} else {
    Write-Host "No autostart shortcut at $shortcutPath"
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if ($python) {
    & $python.Source -m pip uninstall -y profile-auto-launcher
} else {
    Write-Warning "Python not found on PATH - skipping pip uninstall."
}

Write-Host ""
Write-Host "Uninstall complete. Profiles kept at $env:APPDATA\profile-auto-launcher\."
