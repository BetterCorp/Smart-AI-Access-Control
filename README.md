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

## License

AGPL-3.0-only OR Commercial.

Commercial licensing is available only under a separate written agreement with the copyright holder.
