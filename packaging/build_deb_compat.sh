#!/usr/bin/env bash
# Build an Ubuntu 20.04 compatible .deb (runs on Ubuntu 20.04 / 22.04 / 24.04)
# inside Docker via `docker run` (avoids docker build API mismatches).
#
# Usage:
#   bash packaging/build_deb_compat.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

IMAGE="${TIMEFORGE_BUILD_IMAGE:-ubuntu:20.04}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required."
  exit 1
fi

echo "Building compatible .deb inside ${IMAGE} (CPython 3.11 shared, glibc 2.31)"
echo "Output: dist/timeforge_*_amd64.deb and dist/releases/..."

docker run --rm \
  --dns 1.1.1.1 --dns 8.8.8.8 --dns 8.8.4.4 \
  -v "${ROOT}:/src" \
  -w /src \
  -e DEBIAN_FRONTEND=noninteractive \
  -e PYTHONUNBUFFERED=1 \
  "${IMAGE}" \
  bash -lc '
    set -euo pipefail

    apt-get update -qq
    apt-get install -y -qq \
      build-essential curl ca-certificates \
      zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev \
      libssl-dev libreadline-dev libffi-dev libsqlite3-dev \
      libbz2-dev liblzma-dev tk-dev tcl-dev uuid-dev \
      libtk8.6 libtcl8.6 \
      python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
      libappindicator3-1 gir1.2-appindicator3-0.1 \
      libayatana-appindicator3-1 gir1.2-ayatanaappindicator3-0.1 \
      libgtk-3-0 libnotify-bin \
      binutils dpkg-dev fakeroot >/dev/null

    PY=3.11.9
    PYBIN=/opt/python311/bin/python3
    export PATH="/opt/python311/bin:$PATH"
    export LD_LIBRARY_PATH="/opt/python311/lib:${LD_LIBRARY_PATH:-}"

    # PyInstaller needs a shared libpython
    NEED_BUILD=0
    if [ ! -x "$PYBIN" ]; then NEED_BUILD=1; fi
    if [ ! -e /opt/python311/lib/libpython3.11.so ] && [ ! -e /opt/python311/lib/libpython3.11.so.1.0 ]; then
      NEED_BUILD=1
    fi

    if [ "$NEED_BUILD" = 1 ]; then
      echo "Compiling CPython ${PY} with --enable-shared…"
      curl -fsSL "https://www.python.org/ftp/python/${PY}/Python-${PY}.tgz" -o /tmp/Python.tgz
      rm -rf /tmp/Python-src /opt/python311
      tar -xzf /tmp/Python.tgz -C /tmp
      mv "/tmp/Python-${PY}" /tmp/Python-src
      cd /tmp/Python-src
      ./configure --prefix=/opt/python311 --enable-shared \
        LDFLAGS="-Wl,-rpath,/opt/python311/lib"
      make -j"$(nproc)"
      make install
    fi

    export PATH="/opt/python311/bin:$PATH"
    export LD_LIBRARY_PATH="/opt/python311/lib:${LD_LIBRARY_PATH:-}"
    "$PYBIN" -m ensurepip --upgrade || true
    "$PYBIN" -m pip install -q -U pip setuptools wheel

    cd /src
    "$PYBIN" -m pip install -q -r requirements.txt "pyinstaller>=6.0" Pillow
    "$PYBIN" packaging/build.py deb

    BIN=dist/linux/timetrack-agent
    if [ -x "$BIN" ]; then
      echo ""
      echo "Smoke test on Ubuntu 20.04 container:"
      if "$BIN" --help >/dev/null 2>&1; then
        echo "  OK: --help"
      else
        echo "  WARN: --help failed"
      fi
      echo ""
    fi

    echo "Compatible package ready under dist/"
  '

DEB=$(ls -1t dist/timeforge_*_amd64.deb 2>/dev/null | head -1 || true)
if [ -n "${DEB}" ]; then
  echo ""
  echo "Smoke: run binary on Ubuntu 22.04 + 24.04 containers…"
  for IMG in ubuntu:22.04 ubuntu:24.04; do
    if docker run --rm --dns 1.1.1.1 \
      -v "${ROOT}/dist/linux:/b:ro" "${IMG}" \
      bash -lc '/b/timetrack-agent --help >/dev/null 2>&1 && echo "  OK on '"${IMG}"'" || echo "  FAIL on '"${IMG}"'"'
    then
      :
    fi
  done
fi

echo ""
echo "Install on employee PCs:"
echo "  sudo apt install ./dist/timeforge_*_amd64.deb"
