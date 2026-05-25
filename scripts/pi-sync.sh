#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${SMARTAI_APP_DIR:-/opt/smart-ai-access-control}"
DATA_DIR="${SMARTAI_DATA_DIR:-/var/lib/smartai}"
MOCK_INFERENCE="${SMARTAI_MOCK_INFERENCE:-1}"
ENABLE_RELAY_HARDWARE="${SMARTAI_ENABLE_RELAY_HARDWARE:-0}"
ENABLE_HAILO_PACKAGES="${SMARTAI_ENABLE_HAILO_PACKAGES:-1}"
HAILO_APPS_REF="${SMARTAI_HAILO_APPS_REF:-main}"
ENABLE_HAILO_MONITOR="${SMARTAI_ENABLE_HAILO_MONITOR:-1}"
ENABLE_HAILO_SESSION_MONITOR="${SMARTAI_ENABLE_HAILO_SESSION_MONITOR:-0}"
HAILO_MONITOR_INTERVAL_MS="${SMARTAI_HAILO_MONITOR_INTERVAL_MS:-5000}"
ENABLE_PI_FAN_TUNING="${SMARTAI_ENABLE_PI_FAN_TUNING:-1}"
ENABLE_UFW="${SMARTAI_ENABLE_UFW:-1}"
TIMEZONE="${SMARTAI_TIMEZONE:-}"
SERVICE_USER="${SMARTAI_SERVICE_USER:-smartai}"
SERVICE_GROUP="${SMARTAI_SERVICE_GROUP:-smartai}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root:" >&2
  echo "  sudo ${APP_DIR}/scripts/pi-sync.sh" >&2
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
    libbz2-dev
    libdw-dev
    libelf-dev
    liblzma-dev
    libunwind-dev
    meson
    ninja-build
    nodejs
    npm
    portaudio19-dev
    python3-pip
    python3-venv
    usbrelay
    ufw
  )

  if [[ "${ENABLE_HAILO_PACKAGES}" == "1" ]]; then
    packages+=(
      dkms
      hailo-all
      gir1.2-gtk-3.0
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
    prepare_hailo_resources
  fi
}

prepare_hailo_resources() {
  local detection_model="/usr/local/hailo/resources/models/hailo8l/yolov8s.hef"
  local detection_postprocess="/usr/local/hailo/resources/so/libyolo_hailortpp_postprocess.so"

  log "Preparing Hailo detection resources"
  if [[ -f "${detection_model}" && -f "${detection_postprocess}" ]]; then
    "${APP_DIR}/.venv/bin/hailo-post-install" --group detection --skip-download
  else
    "${APP_DIR}/.venv/bin/hailo-post-install" --group detection
  fi

  chgrp -R "${SERVICE_GROUP}" /usr/local/hailo
  chmod -R g+rX /usr/local/hailo
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

configure_pi_fan() {
  if [[ "${ENABLE_PI_FAN_TUNING}" != "1" ]]; then
    return
  fi

  local config_path="/boot/firmware/config.txt"
  if [[ ! -f "${config_path}" ]]; then
    log "Skipping Pi fan tuning; ${config_path} not found"
    return
  fi

  log "Configuring aggressive Raspberry Pi fan curve"
  local tmp
  tmp="$(mktemp)"
  awk '
    BEGIN { skip = 0 }
    /^# BEGIN Smart AI Access Control fan curve$/ { skip = 1; next }
    /^# END Smart AI Access Control fan curve$/ { skip = 0; next }
    skip == 0 { print }
  ' "${config_path}" >"${tmp}"
  cat >>"${tmp}" <<'EOF'

# BEGIN Smart AI Access Control fan curve
# Aggressive Pi 5 active-cooler curve. Values are millicelsius and PWM 0-255.
dtparam=fan_temp0=40000,fan_temp0_hyst=3000,fan_temp0_speed=128
dtparam=fan_temp1=50000,fan_temp1_hyst=3000,fan_temp1_speed=192
dtparam=fan_temp2=60000,fan_temp2_hyst=3000,fan_temp2_speed=224
dtparam=fan_temp3=70000,fan_temp3_hyst=3000,fan_temp3_speed=255
# END Smart AI Access Control fan curve
EOF
  install -m 0755 -d /boot/firmware
  install -m 0644 "${tmp}" "${config_path}"
  rm -f "${tmp}"
}

configure_timezone() {
  if [[ -z "${TIMEZONE}" ]]; then
    return
  fi

  if [[ "${TIMEZONE}" == /* || "${TIMEZONE}" == *..* ]]; then
    echo "Timezone '${TIMEZONE}' must be a zoneinfo name like Africa/Johannesburg" >&2
    exit 1
  fi

  if [[ ! -f "/usr/share/zoneinfo/${TIMEZONE}" ]]; then
    echo "Timezone '${TIMEZONE}' was not found under /usr/share/zoneinfo" >&2
    exit 1
  fi

  log "Configuring OS timezone ${TIMEZONE}"
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone "${TIMEZONE}"
  else
    ln -sfn "/usr/share/zoneinfo/${TIMEZONE}" /etc/localtime
    printf '%s\n' "${TIMEZONE}" >/etc/timezone
  fi
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
  install -m 0644 "${APP_DIR}/deploy/systemd/smartai-cleanup.service" /etc/systemd/system/smartai-cleanup.service
  install -m 0644 "${APP_DIR}/deploy/systemd/smartai-cleanup.timer" /etc/systemd/system/smartai-cleanup.timer

  mkdir -p /etc/systemd/system/smartai-api.service.d
  cat >/etc/systemd/system/smartai-api.service.d/override.conf <<EOF
[Service]
Environment=SMARTAI_DATA_DIR=${DATA_DIR}
Environment=SMARTAI_DB=${DATA_DIR}/smartai.db
Environment=SMARTAI_SNAPSHOT_ROOT=${DATA_DIR}/snapshots
Environment=SMARTAI_ENABLE_HAILO_SESSION_MONITOR=${ENABLE_HAILO_SESSION_MONITOR}
EOF
  if [[ -n "${TIMEZONE}" ]]; then
    cat >>/etc/systemd/system/smartai-api.service.d/override.conf <<EOF
Environment=SMARTAI_TIMEZONE=${TIMEZONE}
EOF
  fi

  mkdir -p /etc/systemd/system/smartai-worker.service.d
  cat >/etc/systemd/system/smartai-worker.service.d/override.conf <<EOF
[Service]
Environment=SMARTAI_DATA_DIR=${DATA_DIR}
Environment=SMARTAI_DB=${DATA_DIR}/smartai.db
Environment=SMARTAI_SNAPSHOT_ROOT=${DATA_DIR}/snapshots
Environment=SMARTAI_MOCK_INFERENCE=${MOCK_INFERENCE}
Environment=SMARTAI_ENABLE_RELAY_HARDWARE=${ENABLE_RELAY_HARDWARE}
Environment=SMARTAI_ENABLE_HAILO_MONITOR=${ENABLE_HAILO_MONITOR}
Environment=SMARTAI_ENABLE_HAILO_SESSION_MONITOR=${ENABLE_HAILO_SESSION_MONITOR}
Environment=HAILO_MONITOR=0
Environment=HAILO_MONITOR_TIME_INTERVAL=${HAILO_MONITOR_INTERVAL_MS}
Environment=HAILORT_LOGGER_PATH=${DATA_DIR}
EOF
  if [[ -n "${TIMEZONE}" ]]; then
    cat >>/etc/systemd/system/smartai-worker.service.d/override.conf <<EOF
Environment=SMARTAI_TIMEZONE=${TIMEZONE}
EOF
  fi

  mkdir -p /etc/systemd/system/smartai-cleanup.service.d
  cat >/etc/systemd/system/smartai-cleanup.service.d/override.conf <<EOF
[Service]
Environment=SMARTAI_DATA_DIR=${DATA_DIR}
Environment=SMARTAI_DB=${DATA_DIR}/smartai.db
Environment=SMARTAI_SNAPSHOT_ROOT=${DATA_DIR}/snapshots
Environment=SMARTAI_LOG_RETENTION_DAYS=${SMARTAI_LOG_RETENTION_DAYS:-7}
Environment=SMARTAI_LOG_MAX_BYTES=${SMARTAI_LOG_MAX_BYTES:-52428800}
Environment=SMARTAI_CLEANUP_SNAPSHOT_BATCH_SIZE=${SMARTAI_CLEANUP_SNAPSHOT_BATCH_SIZE:-500}
EOF
  if [[ -n "${TIMEZONE}" ]]; then
    cat >>/etc/systemd/system/smartai-cleanup.service.d/override.conf <<EOF
Environment=SMARTAI_TIMEZONE=${TIMEZONE}
EOF
  fi

  systemctl daemon-reload
  systemctl enable smartai-api.service smartai-worker.service smartai-cleanup.timer
  systemctl enable --now smartai-cleanup.timer
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
  install_python_app
  verify_hailo_python
  configure_timezone
  configure_pi_fan
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
