#!/bin/sh
# Install built 0.3.10 .deb (needs sudo password once).
set -e
pkill -f '/opt/esstracker/timetrack-agent' 2>/dev/null || true
pkill -f 'timetrack.agent run' 2>/dev/null || true
rm -f "$HOME/.local/share/esstracker/agent.lock"
sudo dpkg -i "$(dirname "$0")/../dist/esstracker_0.3.10_amd64.deb"
echo "Starting esstracker-agent…"
esstracker-agent &
sleep 2
echo "Done. Left-click the ESS tray icon for the menu."
