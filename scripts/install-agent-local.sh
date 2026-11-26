#!/usr/bin/env bash
# Install the esstracker agent .deb built from this repo (needs sudo).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEB="$(ls -t "$ROOT"/dist/esstracker_*_amd64.deb 2>/dev/null | head -1)"
if [ -z "$DEB" ]; then
  echo "No .deb found. Run: ESSTRACKER_FORCE_HOST_BUILD=1 .venv/bin/python packaging/build.py deb"
  exit 1
fi
echo "Installing $DEB"
pkill -f timetrack-agent run 2>/dev/null || true
sleep 1
sudo dpkg -i "$DEB"
echo "Done. Start from Applications → esstracker or: esstracker-agent"
