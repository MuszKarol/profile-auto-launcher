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

$null = & $python.Source -m pip --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> pip not found - installing via ensurepip..."
    & $python.Source -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) {
        throw "ensurepip failed. Install pip manually: https://pip.pypa.io/en/stable/installation/"
    }
}

& $python.Source -m pip install --user --upgrade "$repoDir[full]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit $LASTEXITCODE" }

# Locate palaunch.exe - prefer the user Scripts dir we just installed into
# (an older copy elsewhere on PATH would otherwise shadow the fresh install),
# fall back to PATH.
$userScripts = & $python.Source -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"
$candidate = Join-Path $userScripts "palaunch.exe"
if (Test-Path $candidate) {
    $palaunchPath = $candidate
} else {
    $palaunch = Get-Command palaunch -ErrorAction SilentlyContinue
    if ($palaunch) {
        $palaunchPath = $palaunch.Source
    } else {
        throw "palaunch.exe not found after install. Expected at $candidate."
    }
}
Write-Host "==> palaunch binary: $palaunchPath"

# Add the Scripts dir to the user PATH if not already present.
$currentPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
if ($currentPath -notlike "*$userScripts*") {
    [Environment]::SetEnvironmentVariable('PATH', "$currentPath;$userScripts", 'User')
    Write-Host "==> Added '$userScripts' to your user PATH."
    Write-Host "    Restart your terminal for the change to take effect."
}

# Copy sample profiles into the config dir the app actually reads
# (platformdirs resolves to %LOCALAPPDATA%\profile-auto-launcher on Windows,
# NOT %APPDATA% — hardcoding Roaming here left the profiles undiscovered).
$configDir   = & $python.Source -c "import platformdirs; print(platformdirs.user_config_dir('profile-auto-launcher', appauthor=False))"
$profilesDir = Join-Path $configDir "profiles"
New-Item -ItemType Directory -Force -Path $profilesDir | Out-Null
Get-ChildItem -Path (Join-Path $repoDir "profiles") -Filter "*.yaml" | ForEach-Object {
    $dest = Join-Path $profilesDir $_.Name
    if (-not (Test-Path $dest)) {
        Copy-Item $_.FullName $dest
        Write-Host "    installed profile: $($_.Name)"
    }
}
Write-Host "==> Profiles dir:    $profilesDir"

# Example plugin for `type: plugin` steps.
$repoPlugins = Join-Path $repoDir "plugins"
if (Test-Path $repoPlugins) {
    $pluginsDir = Join-Path $configDir "plugins"
    New-Item -ItemType Directory -Force -Path $pluginsDir | Out-Null
    Get-ChildItem -Path $repoPlugins -Filter "*.py" | ForEach-Object {
        $dest = Join-Path $pluginsDir $_.Name
        if (-not (Test-Path $dest)) {
            Copy-Item $_.FullName $dest
            Write-Host "    installed plugin:  $($_.Name)"
        }
    }
}

# Generate the JSON Schema so the `# yaml-language-server:` modeline that
# `palaunch new` writes resolves to a real file and editors validate as you type.
& $palaunchPath schema | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host "==> Profile JSON Schema written" }

if ($NoAutostart) {
    Write-Host "==> Autostart skipped (-NoAutostart)."
    Write-Host ""
    Write-Host "Done. Start manually with:  palaunch tray"
    return
}

# Create a Startup-folder shortcut that runs `palaunchw tray` — the windowed
# (gui-scripts) binary, so no console window appears at sign-in.
$palaunchwPath = Join-Path (Split-Path $palaunchPath -Parent) "palaunchw.exe"
if (-not (Test-Path $palaunchwPath)) {
    Write-Warning "palaunchw.exe not found next to palaunch.exe; the shortcut will open a console window."
    $palaunchwPath = $palaunchPath
}
$startup      = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup "Profile Auto Launcher.lnk"
$shell        = New-Object -ComObject WScript.Shell
$shortcut     = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath       = $palaunchwPath
$shortcut.Arguments        = "tray"
$shortcut.WorkingDirectory = (Split-Path $palaunchwPath -Parent)
$shortcut.WindowStyle      = 7        # Minimized (harmless for the windowed exe)
$shortcut.Description      = "Profile Auto Launcher (tray)"
$shortcut.Save()

Write-Host "==> Autostart shortcut: $shortcutPath"
Write-Host ""
Write-Host "Installation complete. Launcher will start at your next sign-in."
Write-Host "To start it now, run:  palaunch tray"
