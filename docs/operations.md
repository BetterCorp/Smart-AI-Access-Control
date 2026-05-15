# Operations

## Target OS

Use Raspberry Pi OS Lite 64-bit on Raspberry Pi 5.

## Hailo Verification

```bash
./scripts/verify-hailo.sh
```

Expected: `hailortcli` reports the Hailo device identity.

## Relay Verification

Install `usbrelay`, connect the 4-channel USB relay, then run:

```bash
./scripts/verify-relay.sh
```

The application should run as a non-root `smartai` user with udev permissions for the relay HID device.

## Worker Mode

The systemd template defaults to mock inference and relay dry-run mode:

```text
SMARTAI_MOCK_INFERENCE=1
SMARTAI_ENABLE_RELAY_HARDWARE=0
```

After validating camera inference and relay safety on the Pi, switch these deliberately:

```text
SMARTAI_MOCK_INFERENCE=0
SMARTAI_ENABLE_RELAY_HARDWARE=1
```

## Storage

Snapshots should be stored under `/var/lib/smartai/snapshots` on the Pi. Retention is controlled by both a maximum app byte limit and a minimum free disk percentage.
