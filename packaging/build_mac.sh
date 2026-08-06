#!/usr/bin/env bash
# Build macOS esstracker CLIENT .app + .dmg (MUST run on a Mac)
# Usage:  bash packaging/build_mac.sh
#
# Accuracy notes (Apple + PyInstaller + DeskTime-class trackers):
# - Cannot cross-build a working Mac app from Linux/Windows.
# - Prefer --onedir .app bundle (onefile breaks notarization signing).
# - Gatekeeper requires Developer ID signature + notarization.
# - Users must drag the app to /Applications.
# - Accessibility + Screen Recording TCC permissions are required or
#   window titles / screenshots will be empty (same class of issue DeskTime hits).
# - Build separately for arm64 (Apple Silicon) and x86_64 (Intel) when possible.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: build_mac.sh must run on macOS (this host is $(uname -s))." >&2
  exit 1
fi

ARCH="$(uname -m)"
case "$ARCH" in
  arm64) TAG="arm64" ;;
  x86_64) TAG="x86_64" ;;
  *) TAG="$ARCH" ;;
esac

python3 -m pip install -q -r requirements.txt 'pyinstaller>=6.0' Pillow pyobjc-framework-Quartz 2>/dev/null || true
python3 packaging/build.py dmg --arch "$TAG"

echo ""
echo "Copy the .dmg into dist/releases/ then upload to the server:"
echo "  scp dist/releases/esstracker-*-${TAG}.dmg root@SERVER:/www/wwwroot/timetrack/data/releases/"
