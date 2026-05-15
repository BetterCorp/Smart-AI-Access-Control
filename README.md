# Smart AI Access Control

Headless Raspberry Pi 5 + AI HAT+ access-control appliance for low-FPS RTSP analytics, relay control, and webhook automation.

This first implementation is a working scaffold:

- FastAPI + server-rendered HTML/HTMX web shell.
- SQLite persistence for admin sessions, cameras, AI monitors, relays, rules, events, and settings.
- Dependency-light core domain services for rules, relays, storage, webhooks, and RTSP URL building.
- Plugin-ready AI monitor shape with initial person counting and weapon visibility monitor definitions.
- Worker loop with mockable inference, a Raspberry Pi Hailo person-counting path, snapshot event creation, relay arbitration, and webhook dispatch.
- Raspberry Pi deployment notes and verification scripts.
- Focused tests for the safety-critical logic.

## Rule Model

The system separates camera input, AI interpretation, rules, and outputs:

- Cameras define RTSP connection details.
- AI Monitors run a model or analytic against one camera and publish typed metrics.
- Rules evaluate conditions against one monitor's metrics.
- Outputs perform relay changes, webhooks, snapshots, debounce, cooldown, and fail behavior.

Examples:

- `Person Counter` uses a `YOLOv8 object detector` and publishes `person.count` as a number and `person.present` as a boolean.
- `Weapon Visibility` uses a `custom YOLO-family object detector` and publishes `weapon.visible` as a boolean and `weapon.count` as a number.
- The AI model list also shows which analytics each monitor type is intended for, such as occupancy counting or weapon presence alerting.
- A mantrap rule can evaluate `person.count >= 2`.
- A weapon rule can evaluate `weapon.visible is true`.

## Local Development

```powershell
python -m pip install -e .[dev]
npm ci
npm run build
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

When mock inference is enabled, AI monitor values are simulated and do not come from live camera frames. The Monitors page states the active inference mode and exposes live metric updates plus debug-snapshot slots for real providers.

In real inference mode, `Person Counter` uses the official Hailo detection resources and writes live debug snapshots from the analyzed frame. `Weapon Visibility` still requires a dedicated custom detector before it can run against real camera frames.

## Raspberry Pi Target

Use Raspberry Pi OS Lite 64-bit on the Pi 5. Install the Hailo stack with Raspberry Pi packages, verify with `hailortcli fw-control identify`, then run the app as systemd services using the templates under `deploy/`.

### Pi Setup Process

Recommended base image:

- Raspberry Pi OS Lite 64-bit.
- SSH enabled.
- Wired Ethernet preferred.
- Raspberry Pi AI HAT+ attached before installing the Hailo packages.

The installer is idempotent. Run the same command for a fresh install or to update an existing install. It will:

- install the minimum bootstrap packages, sync the requested Git branch, and re-exec the checked-out latest setup script once before the full install begins,
- install required apt packages,
- create the `smartai` service user,
- create `/var/lib/smartai` for the SQLite DB and snapshots,
- clone or update the repo at `/opt/smart-ai-access-control`,
- create/update the Python virtual environment,
- install Node.js/npm and build TypeScript assets from source,
- install the Debian `usbrelay` CLI used by the relay driver,
- install systemd services,
- install the USB relay udev rule,
- expose the app directly on `0.0.0.0:8000`,
- optionally configure UFW,
- restart services,
- verify `http://127.0.0.1:8000/healthz`.
- install the official `hailo-apps` Python package used by the real person-counting provider when Hailo support is enabled.
- build the app virtualenv with access to system Python packages so apt-installed `gi`/Hailo bindings remain visible.
- run Hailo post-install for the `detection` group so the default HEF, post-process libraries, and environment file exist before the worker starts.

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

Local runs are safe after the bootstrap behavior is present: the script updates the repo first and then re-executes the newly checked-out script before continuing, so it does not keep running stale installer logic after a pull.

Useful installer overrides:

```bash
sudo env \
  SMARTAI_BRANCH=main \
  SMARTAI_MOCK_INFERENCE=1 \
  SMARTAI_ENABLE_RELAY_HARDWARE=0 \
  SMARTAI_ENABLE_HAILO_PACKAGES=1 \
  SMARTAI_HAILO_APPS_REF=main \
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

After switching to real inference, the Cameras page shows the last stream/provider error for each camera. A working person monitor should move from `stream_error` to `online`, publish live metrics on the Monitors page, and begin producing debug snapshots.

The setup script verifies the worker virtualenv can import `gi`, `hailo`, and `hailo_apps` before it restarts services. If that verification fails, setup stops with the missing binding instead of leaving the worker in a hidden half-configured state.

For an existing install created before Hailo support was wired in, rerunning setup repairs an isolated virtualenv by rebuilding it with system packages enabled.

Health separates relay software enablement from relay visibility:

- `Relay control enabled` reflects `SMARTAI_ENABLE_RELAY_HARDWARE`.
- `Relay channels visible` is how many channels the worker can see through the `usbrelay` command.

For the default single-board setup, the relay driver maps the placeholder board id `board-1` to the one detected `usbrelay` board id automatically. Multi-board installs need explicit relay mapping rather than relying on that convenience path.

Frontend build output under `backend/app/static/dist/` is generated during local setup and Pi deployment; it is not committed to the repository.

## License

AGPL-3.0-only OR Commercial.

Commercial licensing is available only under a separate written agreement with the copyright holder.
