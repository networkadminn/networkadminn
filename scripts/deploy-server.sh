#!/usr/bin/env bash
# Run ON the production server (or: ssh root@SERVER 'bash -s' < scripts/deploy-server.sh)
#
# 1) git pull web app code
# 2) reload Gunicorn
# 3) optional: download release installers from GitHub Actions into data/releases/
#
# One-time on server:
#   gh auth login   # or export GH_TOKEN=ghp_... for a fine-grained PAT (repo + actions:read)
#
# Usage:
#   ./scripts/deploy-server.sh
#   ./scripts/deploy-server.sh --tag v0.3.13
#   ./scripts/deploy-server.sh --no-releases

set -euo pipefail

APP_ROOT="${ESSTRACKER_APP_ROOT:-/www/wwwroot/timetrack}"
BRANCH="${ESSTRACKER_DEPLOY_BRANCH:-release/0.3.0}"
RELEASES_DIR="${APP_ROOT}/data/releases"
GUNICORN_PID="${APP_ROOT}/gunicorn.pid"
REPO="${GITHUB_REPOSITORY:-networkadminn/networkadminn}"

TAG=""
SKIP_RELEASES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) TAG="${2:-}"; shift 2 ;;
    --tag=*) TAG="${1#--tag=}"; shift ;;
    --no-releases) SKIP_RELEASES=1; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

echo "==> App root: ${APP_ROOT}"
cd "${APP_ROOT}"

if [[ -d .git ]]; then
  echo "==> Git pull (${BRANCH})"
  git fetch origin "${BRANCH}"
  git checkout "${BRANCH}"
  git pull --ff-only origin "${BRANCH}"
else
  echo "WARN: ${APP_ROOT} is not a git clone — skipping pull (copy files manually or clone repo here)."
fi

if [[ -f "${GUNICORN_PID}" ]]; then
  echo "==> Reload Gunicorn"
  kill -HUP "$(cat "${GUNICORN_PID}")"
else
  echo "WARN: no ${GUNICORN_PID} — reload Gunicorn yourself if needed."
fi

if [[ "${SKIP_RELEASES}" -eq 1 ]]; then
  echo "==> Skipping release artifacts (--no-releases)"
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "WARN: gh not installed — skip release download. Install: https://cli.github.com/"
  exit 0
fi

if ! gh auth status -h github.com >/dev/null 2>&1; then
  echo "WARN: gh not authenticated on this server — skip release download."
  echo "      Run: gh auth login   OR   export GH_TOKEN=..."
  exit 0
fi

if [[ -z "${TAG}" ]]; then
  TAG="$(grep -E '^__version__' timetrack/__init__.py | sed -E 's/.*"([^"]+)".*/\1/')"
  TAG="v${TAG}"
fi

echo "==> Download CI artifacts for ${TAG} into ${RELEASES_DIR}"
mkdir -p "${RELEASES_DIR}"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

# Tag pushes show up as headBranch vX.Y.Z on Actions.
LINUX_RUN="$(gh run list -R "${REPO}" -w "Linux deb" -L 15 --json databaseId,headBranch,conclusion \
  | jq -r --arg t "${TAG}" '.[] | select(.conclusion=="success" and .headBranch==$t) | .databaseId' | head -1)"
WIN_RUN="$(gh run list -R "${REPO}" -w "Windows installer" -L 15 --json databaseId,headBranch,conclusion \
  | jq -r --arg t "${TAG}" '.[] | select(.conclusion=="success" and .headBranch==$t) | .databaseId' | head -1)"

if [[ -z "${LINUX_RUN}" && -z "${WIN_RUN}" ]]; then
  echo "WARN: no successful Linux/Windows runs found for tag/branch ${TAG}. Try: --tag vX.Y.Z"
  exit 0
fi

if [[ -n "${LINUX_RUN}" ]]; then
  gh run download "${LINUX_RUN}" -R "${REPO}" -D "${TMP}/linux" || true
fi
if [[ -n "${WIN_RUN}" ]]; then
  gh run download "${WIN_RUN}" -R "${REPO}" -D "${TMP}/win" || true
fi

find "${TMP}" -type f \( -name '*.deb' -o -name '*.exe' -o -name '*.zip' \) -exec cp -av {} "${RELEASES_DIR}/" \;
echo "==> Releases:"
ls -la "${RELEASES_DIR}"/*"${TAG#v}"* 2>/dev/null || ls -la "${RELEASES_DIR}" | tail -5
