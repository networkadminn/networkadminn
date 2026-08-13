# Install timeforge Windows client (Start Menu + tray autostart).
# Equivalent of the Ubuntu .deb Applications entry + xdg-autostart.
#
# Usage (from the folder that contains timeforge-Agent.exe):
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoAutostart

param(
    [switch]$NoAutostart,
    [switch]$Launch
)

$ErrorActionPreference = "Stop"

$Here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$AgentSrc = Join-Path $Here "timeforge-Agent.exe"
if (-not (Test-Path $AgentSrc)) {
    throw "timeforge-Agent.exe not found next to install.ps1 ($Here)"
}

$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\timeforge"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Write-Host "[timeforge] Installing to $InstallDir ..."
Copy-Item $AgentSrc (Join-Path $InstallDir "timeforge-Agent.exe") -Force

$DefaultsSrc = Join-Path $Here "defaults.toml"
if (Test-Path $DefaultsSrc) {
    Copy-Item $DefaultsSrc (Join-Path $InstallDir "defaults.toml") -Force
}

$UninstallSrc = Join-Path $Here "uninstall.ps1"
if (Test-Path $UninstallSrc) {
    Copy-Item $UninstallSrc (Join-Path $InstallDir "uninstall.ps1") -Force
}

$AgentExe = Join-Path $InstallDir "timeforge-Agent.exe"

# Start Menu shortcut (Ubuntu Applications menu equivalent)
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Item -ItemType Directory -Force -Path $StartMenu | Out-Null
$ShortcutPath = Join-Path $StartMenu "timeforge.lnk"

$Wsh = New-Object -ComObject WScript.Shell
$Sc = $Wsh.CreateShortcut($ShortcutPath)
$Sc.TargetPath = $AgentExe
$Sc.WorkingDirectory = $InstallDir
$Sc.WindowStyle = 7
$Sc.Description = "timeforge - Sign in and track activity"
$Sc.IconLocation = "$AgentExe,0"
$Sc.Save()
Write-Host "[timeforge] Start Menu: timeforge"

if (-not $NoAutostart) {
    $Startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
    New-Item -ItemType Directory -Force -Path $Startup | Out-Null
    $AutoPath = Join-Path $Startup "timeforge.lnk"
    $Auto = $Wsh.CreateShortcut($AutoPath)
    $Auto.TargetPath = $AgentExe
    $Auto.WorkingDirectory = $InstallDir
    $Auto.WindowStyle = 7
    $Auto.Description = "timeforge autostart"
    $Auto.IconLocation = "$AgentExe,0"
    $Auto.Save()
    Write-Host "[timeforge] Autostart at logon enabled"
}

Write-Host ""
Write-Host "  timeforge client installed."
Write-Host "  Open Start -> timeforge -> Sign in"
Write-Host "  Tray icon appears in the notification area."
Write-Host ("  Uninstall: powershell -ExecutionPolicy Bypass -File `"{0}\uninstall.ps1`"" -f $InstallDir)
Write-Host ""

if ($Launch) {
    Start-Process -FilePath $AgentExe -WorkingDirectory $InstallDir
}
