#!/usr/bin/env bash
set -euo pipefail

BRANCH="${SMARTAI_BRANCH:-main}"
APP_DIR="${SMARTAI_APP_DIR:-/opt/smart-ai-access-control}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ${APP_DIR}/scripts/pi-pull-update.sh" >&2
  exit 1
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "${APP_DIR} is not a git checkout. Run scripts/pi-setup.sh first." >&2
  exit 1
fi

git -C "${APP_DIR}" fetch origin "${BRANCH}"
git -C "${APP_DIR}" checkout "${BRANCH}"
git -C "${APP_DIR}" reset --hard "origin/${BRANCH}"

"${APP_DIR}/.venv/bin/python" -m pip install -e "${APP_DIR}"

if command -v npm >/dev/null 2>&1; then
  npm --prefix "${APP_DIR}" ci
  npm --prefix "${APP_DIR}" run build
  rm -rf "${APP_DIR}/node_modules"
fi

systemctl daemon-reload
systemctl restart smartai-api.service smartai-worker.service
systemctl reload caddy || systemctl restart caddy

curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/healthz >/dev/null
echo "Smart AI Access Control updated and healthy."
