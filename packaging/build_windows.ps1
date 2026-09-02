# Build Windows esstracker CLIENT .exe (run on Windows)
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Produces (same role as Ubuntu .deb):
#   dist/windows/esstracker-Agent.exe   — tray client
#   dist/windows/defaults.toml          — company server URL
#   dist/windows/install.ps1            — Start Menu + autostart
#   dist/releases/esstracker-*-windows.zip
#
# Accuracy notes:
# - Build ON Windows — Linux cannot produce a real PE .exe.
# - SmartScreen may warn until the binary is Authenticode-signed.
# - Antivirus sometimes false-flags PyInstaller onefile bundles.
# - Tracking uses Win32 foreground window + last-input idle (reliable on Win10/11).

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Py = Get-Command py -ErrorAction SilentlyContinue
if (-not (Test-Path ".venv")) {
    if ($Py) {
        py -3 -m venv .venv
    } else {
        python -m venv .venv
    }
}

$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
& $VenvPy -m pip install -U pip
& $VenvPy -m pip install -r requirements.txt "pyinstaller>=6.0" "pywin32" "Pillow" "pystray"
& $VenvPy packaging\build.py exe-client

$WinDir = Join-Path $Root "dist\windows"
Write-Host ""
Write-Host "Done. Windows client kit:"
Write-Host "  $WinDir\esstracker-Agent.exe"
Write-Host "  $WinDir\install.ps1"
Write-Host "  dist\releases\esstracker-*-windows.zip"
Write-Host ""
Write-Host "Install on this PC:"
Write-Host "  powershell -ExecutionPolicy Bypass -File dist\windows\install.ps1 -Launch"
Write-Host ""
Write-Host "Upload zip/exe to server data/releases/ for the /download page."
