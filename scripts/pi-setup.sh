#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${SMARTAI_REPO_URL:-https://github.com/BetterCorp/Smart-AI-Access-Control.git}"
BRANCH="${SMARTAI_BRANCH:-main}"
APP_DIR="${SMARTAI_APP_DIR:-/opt/smart-ai-access-control}"
DATA_DIR="${SMARTAI_DATA_DIR:-/var/lib/smartai}"
MOCK_INFERENCE="${SMARTAI_MOCK_INFERENCE:-1}"
ENABLE_RELAY_HARDWARE="${SMARTAI_ENABLE_RELAY_HARDWARE:-0}"
ENABLE_HAILO_PACKAGES="${SMARTAI_ENABLE_HAILO_PACKAGES:-1}"
HAILO_APPS_REF="${SMARTAI_HAILO_APPS_REF:-main}"
ENABLE_UFW="${SMARTAI_ENABLE_UFW:-1}"
SERVICE_USER="${SMARTAI_SERVICE_USER:-smartai}"
SERVICE_GROUP="${SMARTAI_SERVICE_GROUP:-smartai}"

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

apt_install() {
  local packages=(
    ca-certificates
    curl
    git
    nodejs
    npm
    python3-pip
    python3-venv
    ufw
  )

  if [[ "${ENABLE_HAILO_PACKAGES}" == "1" ]]; then
    packages+=(
      dkms
      hailo-all
      python3-gi
      python3-gi-cairo
      gir1.2-gstreamer-1.0
      rpicam-apps
    )
  fi

  log "Installing system packages"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
}

ensure_user_and_dirs() {
  log "Creating service user and data directories"
  if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "${DATA_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
  fi

  install -d -o root -g root -m 0755 "$(dirname "${APP_DIR}")"
  install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0750 "${DATA_DIR}"
  install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0750 "${DATA_DIR}/snapshots"
}

sync_repo() {
  log "Syncing repository ${REPO_URL} (${BRANCH})"
  require_command git

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

install_python_app() {
  log "Installing Python application"
  if [[ "${ENABLE_HAILO_PACKAGES}" == "1" ]]; then
    if [[ -x "${APP_DIR}/.venv/bin/python" ]] && grep -qx "include-system-site-packages = true" "${APP_DIR}/.venv/pyvenv.cfg"; then
      python3 -m venv --upgrade --system-site-packages "${APP_DIR}/.venv"
    elif [[ -x "${APP_DIR}/.venv/bin/python" ]]; then
      log "Rebuilding virtualenv with system site packages enabled"
      python3 -m venv --clear --system-site-packages "${APP_DIR}/.venv"
    else
      python3 -m venv --system-site-packages "${APP_DIR}/.venv"
    fi
  else
    if [[ -x "${APP_DIR}/.venv/bin/python" ]]; then
      python3 -m venv --upgrade "${APP_DIR}/.venv"
    else
      python3 -m venv "${APP_DIR}/.venv"
    fi
  fi
  "${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
  "${APP_DIR}/.venv/bin/pip" install -e "${APP_DIR}"
  if [[ "${ENABLE_HAILO_PACKAGES}" == "1" ]]; then
    log "Installing official Hailo Apps Python package"
    "${APP_DIR}/.venv/bin/pip" install "hailo-apps @ git+https://github.com/hailo-ai/hailo-apps.git@${HAILO_APPS_REF}"
  fi
}

verify_hailo_python() {
  if [[ "${ENABLE_HAILO_PACKAGES}" != "1" ]]; then
    return
  fi

  log "Verifying Hailo Python bindings"
  "${APP_DIR}/.venv/bin/python" - <<'PY'
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
import hailo
import hailo_apps

print("Hailo Python bindings available")
PY
}

install_node_assets() {
  log "Building TypeScript assets"
  require_command npm
  npm --prefix "${APP_DIR}" ci
  npm --prefix "${APP_DIR}" run build
  rm -rf "${APP_DIR}/node_modules"
}

install_systemd() {
  log "Installing systemd services"
  install -m 0644 "${APP_DIR}/deploy/systemd/smartai-api.service" /etc/systemd/system/smartai-api.service
  install -m 0644 "${APP_DIR}/deploy/systemd/smartai-worker.service" /etc/systemd/system/smartai-worker.service

  mkdir -p /etc/systemd/system/smartai-worker.service.d
  cat >/etc/systemd/system/smartai-worker.service.d/override.conf <<EOF
[Service]
Environment=SMARTAI_MOCK_INFERENCE=${MOCK_INFERENCE}
Environment=SMARTAI_ENABLE_RELAY_HARDWARE=${ENABLE_RELAY_HARDWARE}
EOF

  systemctl daemon-reload
  systemctl enable smartai-api.service smartai-worker.service
  systemctl restart smartai-api.service smartai-worker.service
}

install_udev() {
  log "Installing USB relay udev rule"
  install -m 0644 "${APP_DIR}/deploy/udev/99-usbrelay.rules" /etc/udev/rules.d/99-usbrelay.rules
  udevadm control --reload-rules
  udevadm trigger || true
}

configure_firewall() {
  if [[ "${ENABLE_UFW}" != "1" ]]; then
    return
  fi

  log "Configuring firewall"
  ufw allow OpenSSH
  ufw allow 8000/tcp
  ufw --force enable
}

verify_install() {
  log "Verifying local API"
  curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8000/healthz >/dev/null || {
    echo "API health check failed. Recent service logs:" >&2
    journalctl -u smartai-api.service -n 80 --no-pager >&2 || true
    exit 1
  }

  if [[ "${ENABLE_HAILO_PACKAGES}" == "1" ]] && command -v hailortcli >/dev/null 2>&1; then
    log "Checking Hailo device visibility"
    hailortcli fw-control identify || true
  fi
}

main() {
  apt_install
  ensure_user_and_dirs
  sync_repo
  install_python_app
  verify_hailo_python
  install_node_assets
  install_udev
  install_systemd
  configure_firewall
  verify_install

  log "Setup complete"
  echo "Open: http://<pi-ip>:8000"
  echo "First-run admin setup: http://<pi-ip>:8000/bootstrap"
  echo "Local health check: http://127.0.0.1:8000/healthz"
}

main "$@"
