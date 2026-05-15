#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y python3-venv python3-pip dkms hailo-all rpicam-apps caddy git ufw

sudo useradd --system --create-home --home-dir /var/lib/smartai --shell /usr/sbin/nologin smartai || true
sudo mkdir -p /opt/smart-ai-access-control /var/lib/smartai/snapshots
sudo chown -R smartai:smartai /var/lib/smartai
sudo ufw allow OpenSSH || true
sudo ufw allow 443/tcp || true

echo "Copy the repository to /opt/smart-ai-access-control, then run:"
echo "  python3 -m venv /opt/smart-ai-access-control/.venv"
echo "  /opt/smart-ai-access-control/.venv/bin/pip install -e /opt/smart-ai-access-control"
echo "  sudo cp deploy/systemd/*.service /etc/systemd/system/"
echo "  sudo cp deploy/udev/99-usbrelay.rules /etc/udev/rules.d/"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now smartai-api"
