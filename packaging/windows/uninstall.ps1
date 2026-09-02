# Remove esstracker Windows client (Start Menu + Startup + install folder).

$ErrorActionPreference = "Stop"

$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\esstracker"
$StartMenuLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\esstracker.lnk"
$StartupLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\esstracker.lnk"

# Stop running agent if present
Get-Process -Name "esstracker-Agent" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Remove-Item $StartMenuLnk -Force -ErrorAction SilentlyContinue
Remove-Item $StartupLnk -Force -ErrorAction SilentlyContinue

if (Test-Path $InstallDir) {
    Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[esstracker] Uninstalled."
Write-Host "Note: user config/data under %APPDATA%\esstracker and %LOCALAPPDATA%\esstracker was kept."
