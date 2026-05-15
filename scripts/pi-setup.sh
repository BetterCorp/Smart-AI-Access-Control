#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${SMARTAI_REPO_URL:-https://github.com/BetterCorp/Smart-AI-Access-Control.git}"
BRANCH="${SMARTAI_BRANCH:-main}"
APP_DIR="${SMARTAI_APP_DIR:-/opt/smart-ai-access-control}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root so this works both from a file and from curl | bash:" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/BetterCorp/Smart-AI-Access-Control/main/scripts/pi-setup.sh | sudo bash" >&2
  echo "or:" >&2
  echo "  sudo scripts/pi-setup.sh" >&2
  exit 1
fi

log() {
  printf '\n==> %s\n' "$*"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

bootstrap_install() {
  log "Installing bootstrap packages"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git
}

sync_repo() {
  log "Syncing repository ${REPO_URL} (${BRANCH})"
  require_command git
  install -d -o root -g root -m 0755 "$(dirname "${APP_DIR}")"

  if [[ -d "${APP_DIR}/.git" ]]; then
    if git -C "${APP_DIR}" remote get-url origin >/dev/null 2>&1; then
      git -C "${APP_DIR}" remote set-url origin "${REPO_URL}"
    else
      git -C "${APP_DIR}" remote add origin "${REPO_URL}"
    fi
    git -C "${APP_DIR}" fetch origin "${BRANCH}"
    git -C "${APP_DIR}" checkout "${BRANCH}"
    git -C "${APP_DIR}" reset --hard "origin/${BRANCH}"
  elif [[ -d "${APP_DIR}" ]] && find "${APP_DIR}" -mindepth 1 -maxdepth 1 | read -r; then
    echo "${APP_DIR} exists and is not an empty git checkout. Aborting." >&2
    echo "Move it aside or set SMARTAI_APP_DIR to another path." >&2
    exit 1
  else
    git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
  fi

  chown -R root:root "${APP_DIR}"
}

main() {
  bootstrap_install
  sync_repo

  if [[ ! -x "${APP_DIR}/scripts/pi-sync.sh" ]]; then
    echo "Latest sync script not found at ${APP_DIR}/scripts/pi-sync.sh" >&2
    exit 1
  fi

  log "Running latest checked-out sync script"
  exec "${APP_DIR}/scripts/pi-sync.sh"
}

main "$@"
