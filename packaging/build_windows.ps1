# Build Windows esstracker CLIENT .exe (run on Windows)
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Accuracy notes (from production trackers / PyInstaller):
# - Build ON Windows — Linux cannot produce a real PE .exe.
# - SmartScreen may warn until the binary is Authenticode-signed.
# - Antivirus sometimes false-flags PyInstaller onefile bundles.
# - Tracking uses Win32 foreground window + last-input idle (reliable on Win10/11).

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Test-Path ".venv")) {
    py -3.11 -m venv .venv
    if (-not $?) { python -m venv .venv }
}

& .\.venv\Scripts\python.exe -m pip install -U pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt "pyinstaller>=6.0" "pywin32" "Pillow"
& .\.venv\Scripts\python.exe packaging\build.py exe-client

$Rel = Join-Path $Root "dist\releases"
New-Item -ItemType Directory -Force -Path $Rel | Out-Null
$Built = Get-ChildItem "dist\windows\esstracker-Agent.exe","dist\windows\esstracker-Setup.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Built) {
    Copy-Item $Built.FullName (Join-Path $Rel $Built.Name) -Force
    Write-Host "Copied to dist\releases\$($Built.Name)"
}

Write-Host ""
Write-Host "Done. Place the .exe under dist/releases/ (or server data/releases/) for the download page."
Write-Host "Upload to: /www/wwwroot/timetrack/data/releases/"
