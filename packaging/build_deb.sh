#!/usr/bin/env bash
# Build Linux .deb for esstracker (Applications menu + tray + ESS logo)
#
# For a package that installs cleanly on Ubuntu 20.04, 22.04, AND 24.04,
# prefer:  bash packaging/build_deb_compat.sh  (Docker / Ubuntu 20.04)
#
# This host script builds on the current Ubuntu (fine if that is 20.04).
#
# Usage:  bash packaging/build_deb.sh
set -euo pipefail
cd "$(dirname "$0")/.."

HOST_VER="$(. /etc/os-release 2>/dev/null; echo "${VERSION_ID:-unknown}")"
echo "Building on Ubuntu ${HOST_VER}"
case "${HOST_VER}" in
  20.04*|20.10*)
    echo "Good: Ubuntu 20.04 builds run on 20 / 22 / 24."
    ;;
  22.04*|22.10*)
    echo "Note: binary may not start on Ubuntu 20.04. Use build_deb_compat.sh for full support."
    ;;
  24.04*|24.10*|25.*)
    echo "STOP: Ubuntu 24 host embeds GLIBC 2.38 — package will NOT run on 20.04/22.04."
    echo "Use instead:  bash packaging/build_deb_compat.sh"
    if [ "${ESSTRACKER_FORCE_HOST_BUILD:-}" != "1" ]; then
      exit 1
    fi
    echo "ESSTRACKER_FORCE_HOST_BUILD=1 set — continuing anyway (24.04-only clients)."
    ;;
esac

python3 -m pip install -q -r requirements.txt pyinstaller Pillow 2>/dev/null || true
python3 packaging/build.py deb

echo ""
echo "Done. Package is under dist/esstracker_*.deb"
echo "Install:  sudo apt install ./dist/esstracker_*.deb"
echo "Then open Applications → esstracker (ESS logo in the tray)"
