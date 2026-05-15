# Smart AI Access Control

Headless Raspberry Pi 5 + AI HAT+ access-control appliance for low-FPS RTSP analytics, relay control, and webhook automation.

This first implementation is a working scaffold:

- FastAPI + server-rendered HTML/HTMX web shell.
- SQLite persistence for admin sessions, cameras, relays, rules, events, and settings.
- Dependency-light core domain services for rules, relays, storage, webhooks, and RTSP URL building.
- Plugin-ready analytics shape with an initial object/person counting plugin.
- Worker loop with mockable inference, snapshot event creation, relay arbitration, and webhook dispatch.
- Raspberry Pi deployment notes and verification scripts.
- Focused tests for the safety-critical logic.

## Local Development

```powershell
python -m pip install -e .[dev]
python -m pytest
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

On first launch, create the single admin password at `/bootstrap`.

## Runtime Environment

Useful environment variables:

```text
SMARTAI_DATA_DIR=.smartai
SMARTAI_DB=.smartai/smartai.db
SMARTAI_SNAPSHOT_ROOT=.smartai/snapshots
SMARTAI_MOCK_INFERENCE=1
SMARTAI_ENABLE_RELAY_HARDWARE=0
```

Use mock inference during development. On the Pi, the relay hardware is only driven when `SMARTAI_ENABLE_RELAY_HARDWARE=1`.

## Raspberry Pi Target

Use Raspberry Pi OS Lite 64-bit on the Pi 5. Install the Hailo stack with Raspberry Pi packages, verify with `hailortcli fw-control identify`, then run the app as systemd services using the templates under `deploy/`.

### Pi Setup Process

Recommended base image:

- Raspberry Pi OS Lite 64-bit.
- SSH enabled.
- Wired Ethernet preferred.
- Raspberry Pi AI HAT+ attached before installing the Hailo packages.

The installer is idempotent. Run the same command for a fresh install or to update an existing install. It will:

- install required apt packages,
- create the `smartai` service user,
- create `/var/lib/smartai` for the SQLite DB and snapshots,
- clone or update the repo at `/opt/smart-ai-access-control`,
- create/update the Python virtual environment,
- build TypeScript assets if `npm` is available,
- install systemd services,
- install the USB relay udev rule,
- expose the app directly on `0.0.0.0:8000`,
- optionally configure UFW,
- restart services,
- verify `http://127.0.0.1:8000/healthz`.

Fresh install or update from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/BetterCorp/Smart-AI-Access-Control/main/scripts/pi-setup.sh | sudo bash
```

The repo is public, so the installer uses HTTPS git checkout by default. If you need to override the repo URL, run:

```bash
sudo env SMARTAI_REPO_URL=https://github.com/BetterCorp/Smart-AI-Access-Control.git scripts/pi-setup.sh
```

or pipe the script while preserving the override:

```bash
curl -fsSL https://raw.githubusercontent.com/BetterCorp/Smart-AI-Access-Control/main/scripts/pi-setup.sh \
  | sudo env SMARTAI_REPO_URL=https://github.com/BetterCorp/Smart-AI-Access-Control.git bash
```

Run again any time to pull and apply the latest code:

```bash
sudo /opt/smart-ai-access-control/scripts/pi-setup.sh
```

Useful installer overrides:

```bash
sudo env \
  SMARTAI_BRANCH=main \
  SMARTAI_MOCK_INFERENCE=1 \
  SMARTAI_ENABLE_RELAY_HARDWARE=0 \
  SMARTAI_ENABLE_HAILO_PACKAGES=1 \
  SMARTAI_ENABLE_UFW=1 \
  /opt/smart-ai-access-control/scripts/pi-setup.sh
```

After setup:

```bash
systemctl status smartai-api smartai-worker
curl http://127.0.0.1:8000/healthz
```

Open from another machine on the LAN:

```text
http://<pi-ip>:8000/bootstrap
```

Use `/bootstrap` once to create the single admin password.

Production switchovers after hardware validation:

```bash
sudo env SMARTAI_MOCK_INFERENCE=0 SMARTAI_ENABLE_RELAY_HARDWARE=1 /opt/smart-ai-access-control/scripts/pi-setup.sh
```

## License

AGPL-3.0-only OR Commercial.

Commercial licensing is available only under a separate written agreement with the copyright holder.
