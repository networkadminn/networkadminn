#!/usr/bin/env bash
# Upload a built macOS .dmg to GitHub Releases and/or the production download server.
#
# GitHub (needs GITHUB_TOKEN or `gh auth login`):
#   ./scripts/publish-macos-dmg.sh dist/releases/esstracker-0.3.14-x86_64.dmg
#
# Production download page (needs SSH to server):
#   ./scripts/publish-macos-dmg.sh --server dist/releases/esstracker-0.3.14-x86_64.dmg
#
# Both:
#   ./scripts/publish-macos-dmg.sh --github --server path/to.dmg

set -euo pipefail

REPO="${GITHUB_REPOSITORY:-networkadminn/networkadminn}"
SERVER="${ESSTRACKER_RELEASES_HOST:-root@31.97.239.250}"
REMOTE_DIR="${ESSTRACKER_RELEASES_DIR:-/www/wwwroot/timetrack/data/releases}"

DO_GITHUB=0
DO_SERVER=0
DMG=""

usage() {
  sed -n '2,12p' "$0"
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --github) DO_GITHUB=1; shift ;;
    --server) DO_SERVER=1; shift ;;
    -h|--help) usage 0 ;;
    *)
      if [[ -z "${DMG}" ]]; then
        DMG="$1"
        shift
      else
        echo "Unexpected argument: $1" >&2
        usage 1
      fi
      ;;
  esac
done

if [[ -z "${DMG}" ]]; then
  echo "Missing .dmg path" >&2
  usage 1
fi
if [[ ! -f "${DMG}" ]]; then
  echo "File not found: ${DMG}" >&2
  exit 1
fi

if [[ "${DO_GITHUB}" -eq 0 && "${DO_SERVER}" -eq 0 ]]; then
  DO_GITHUB=1
fi

basename="$(basename "${DMG}")"
# esstracker-0.3.14-x86_64.dmg → v0.3.14
ver="$(echo "${basename}" | sed -nE 's/^esstracker-([0-9]+\.[0-9]+\.[0-9]+)-.*/\1/p')"
if [[ -z "${ver}" ]]; then
  echo "Could not parse version from filename: ${basename}" >&2
  exit 1
fi
TAG="v${ver}"

github_upload() {
  if command -v gh >/dev/null 2>&1; then
    if ! gh auth status -h github.com >/dev/null 2>&1; then
      echo "Run: gh auth login" >&2
      exit 1
    fi
    if ! gh release view "${TAG}" -R "${REPO}" >/dev/null 2>&1; then
      gh release create "${TAG}" -R "${REPO}" \
        --title "esstracker ${TAG}" \
        --notes "macOS employee client ${TAG}. Intel: *-x86_64.dmg. Apple Silicon: *-arm64.dmg (CI)."
    fi
    gh release upload "${TAG}" -R "${REPO}" "${DMG}" --clobber
    gh release view "${TAG}" -R "${REPO}" --web=false
    return
  fi

  local token="${GITHUB_TOKEN:-}"
  if [[ -z "${token}" ]]; then
    echo "Set GITHUB_TOKEN or install and authenticate gh (https://cli.github.com/)." >&2
    exit 1
  fi

  local api="https://api.github.com/repos/${REPO}/releases"
  local release_id
  release_id="$(curl -fsSL -H "Authorization: Bearer ${token}" -H "Accept: application/vnd.github+json" \
    "${api}/tags/${TAG}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || true)"
  if [[ -z "${release_id}" || "${release_id}" == "None" ]]; then
    release_id="$(curl -fsSL -X POST -H "Authorization: Bearer ${token}" -H "Accept: application/vnd.github+json" \
      -d "$(python3 -c "import json; print(json.dumps({'tag_name':'${TAG}','name':'esstracker ${TAG}','body':'macOS employee client ${TAG}.','draft':False,'generate_release_notes':True}))")" \
      "${api}" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")"
  fi

  curl -fsSL -X POST \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/vnd.github+json" \
    -H "Content-Type: application/octet-stream" \
    --data-binary @"${DMG}" \
    "https://uploads.github.com/repos/${REPO}/releases/${release_id}/assets?name=${basename}"
  echo "Uploaded ${basename} to GitHub release ${TAG}"
}

server_upload() {
  echo "==> scp ${DMG} → ${SERVER}:${REMOTE_DIR}/"
  scp "${DMG}" "${SERVER}:${REMOTE_DIR}/"
  echo "==> Public download: https://tracker.euclideesolutions.com/download"
}

if [[ "${DO_GITHUB}" -eq 1 ]]; then
  echo "==> GitHub Releases (${TAG})"
  github_upload
fi
if [[ "${DO_SERVER}" -eq 1 ]]; then
  server_upload
fi
