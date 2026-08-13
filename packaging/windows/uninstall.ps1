# Remove timeforge Windows client (Start Menu + Startup + install folder).

$ErrorActionPreference = "Stop"

$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\timeforge"
$StartMenuLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\timeforge.lnk"
$StartupLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\timeforge.lnk"

# Stop running agent if present
Get-Process -Name "timeforge-Agent" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Remove-Item $StartMenuLnk -Force -ErrorAction SilentlyContinue
Remove-Item $StartupLnk -Force -ErrorAction SilentlyContinue

if (Test-Path $InstallDir) {
    Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[timeforge] Uninstalled."
Write-Host "Note: user config/data under %APPDATA%\timeforge and %LOCALAPPDATA%\timeforge was kept."
