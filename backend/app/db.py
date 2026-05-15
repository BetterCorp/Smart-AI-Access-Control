from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.app.domain import (
    Action,
    CameraConfig,
    ConditionGroup,
    FailPolicy,
    MonitorConfig,
    Observation,
    RelayAction,
    RelayDesiredState,
    RuleCondition,
    RuleConfig,
    RuleState,
    SnapshotDelivery,
    SnapshotRef,
    WebhookAction,
)
from backend.app.relay.service import RelayChannelConfig


SCHEMA_VERSION = 1


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS admin_user (
                  id INTEGER PRIMARY KEY CHECK (id = 1),
                  password_hash TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                  token TEXT PRIMARY KEY,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cameras (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  host TEXT NOT NULL,
                  port INTEGER NOT NULL,
                  username TEXT,
                  password TEXT,
                  path TEXT NOT NULL,
                  transport TEXT NOT NULL,
                  analytics_fps REAL NOT NULL,
                  health TEXT NOT NULL DEFAULT 'unknown',
                  last_error TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS relay_channels (
                  id TEXT PRIMARY KEY,
                  board_id TEXT NOT NULL,
                  channel_number INTEGER NOT NULL,
                  name TEXT NOT NULL,
                  default_state TEXT NOT NULL,
                  global_fail_state TEXT NOT NULL,
                  current_state TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS monitors (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  model_id TEXT NOT NULL,
                  camera_id TEXT NOT NULL,
                  enabled INTEGER NOT NULL,
                  config_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS monitor_states (
                  monitor_id TEXT NOT NULL,
                  metric TEXT NOT NULL,
                  value TEXT NOT NULL,
                  value_json TEXT NOT NULL,
                  observed_at TEXT NOT NULL,
                  labels_json TEXT NOT NULL,
                  PRIMARY KEY (monitor_id, metric)
                );

                CREATE TABLE IF NOT EXISTS monitor_debug_snapshots (
                  monitor_id TEXT PRIMARY KEY,
                  path TEXT NOT NULL,
                  mime_type TEXT NOT NULL,
                  observed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rules (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  enabled INTEGER NOT NULL,
                  priority INTEGER NOT NULL,
                  monitor_id TEXT,
                  camera_ids_json TEXT NOT NULL,
                  plugin_id TEXT NOT NULL,
                  condition_group_json TEXT,
                  condition_json TEXT NOT NULL,
                  true_actions_json TEXT NOT NULL,
                  false_actions_json TEXT NOT NULL,
                  fault_actions_json TEXT NOT NULL,
                  debounce_true_ms INTEGER NOT NULL,
                  debounce_false_ms INTEGER NOT NULL,
                  cooldown_ms INTEGER NOT NULL,
                  fail_policy TEXT NOT NULL,
                  state TEXT NOT NULL DEFAULT 'unknown',
                  last_observation_value TEXT,
                  last_dedupe_key TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                  id TEXT PRIMARY KEY,
                  camera_id TEXT NOT NULL,
                  rule_id TEXT NOT NULL,
                  state TEXT NOT NULL,
                  metric TEXT NOT NULL,
                  value TEXT NOT NULL,
                  observation_json TEXT NOT NULL,
                  snapshot_path TEXT,
                  snapshot_sha256 TEXT,
                  webhook_status TEXT NOT NULL DEFAULT 'not_sent',
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                """
            )
            self._add_column(conn, "rules", "monitor_id", "TEXT")
            self._add_column(conn, "rules", "condition_group_json", "TEXT")
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()
        self.ensure_default_relays()
        self.ensure_default_settings()

    def _add_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def ensure_default_relays(self) -> None:
        with self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM relay_channels").fetchone()[0]
            if count:
                return
            now = utc_iso()
            for index in range(1, 5):
                conn.execute(
                    """
                    INSERT INTO relay_channels
                      (id, board_id, channel_number, name, default_state, global_fail_state, current_state, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"relay-{index}",
                        "board-1",
                        index,
                        f"Relay {index}",
                        RelayDesiredState.OFF.value,
                        FailPolicy.FAIL_OFF.value,
                        RelayDesiredState.OFF.value,
                        now,
                    ),
                )

    def ensure_default_settings(self) -> None:
        defaults = {
            "global_max_inference_fps": "5",
            "snapshot_max_bytes": str(10 * 1024 * 1024 * 1024),
            "snapshot_min_free_disk_percent": "20",
            "webhook_url": "",
            "webhook_snapshot_delivery": SnapshotDelivery.MULTIPART.value,
            "webhook_hmac_secret": "",
        }
        with self.connect() as conn:
            for key, value in defaults.items():
                conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def is_bootstrapped(self) -> bool:
        with self.db.connect() as conn:
            return conn.execute("SELECT 1 FROM admin_user WHERE id = 1").fetchone() is not None

    def bootstrap_admin(self, password: str, iterations: int = 260_000) -> None:
        if self.is_bootstrapped():
            raise ValueError("admin user already exists")
        payload = hash_password(password, iterations=iterations)
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO admin_user (id, password_hash, created_at) VALUES (1, ?, ?)",
                (json.dumps(payload), utc_iso()),
            )

    def verify_admin(self, password: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute("SELECT password_hash FROM admin_user WHERE id = 1").fetchone()
        if row is None:
            return False
        payload = json.loads(row["password_hash"])
        candidate = hash_password(password, salt=payload["salt"], iterations=int(payload["iterations"]))
        return hmac.compare_digest(payload["digest"], candidate["digest"])

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.db.connect() as conn:
            conn.execute("INSERT INTO sessions (token, created_at) VALUES (?, ?)", (token, utc_iso()))
        return token

    def has_session(self, token: str | None) -> bool:
        if not token:
            return False
        with self.db.connect() as conn:
            return conn.execute("SELECT 1 FROM sessions WHERE token = ?", (token,)).fetchone() is not None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self.db.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def list_cameras(self) -> list[CameraConfig]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM cameras ORDER BY name").fetchall()
        return [camera_from_row(row) for row in rows]

    def get_camera(self, camera_id: str) -> CameraConfig | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        return camera_from_row(row) if row else None

    def save_camera(self, camera: CameraConfig, enabled: bool = True) -> None:
        now = utc_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO cameras
                  (id, name, enabled, host, port, username, password, path, transport, analytics_fps, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name = excluded.name,
                  enabled = excluded.enabled,
                  host = excluded.host,
                  port = excluded.port,
                  username = excluded.username,
                  password = excluded.password,
                  path = excluded.path,
                  transport = excluded.transport,
                  analytics_fps = excluded.analytics_fps,
                  updated_at = excluded.updated_at
                """,
                (
                    camera.id,
                    camera.name,
                    1 if enabled else 0,
                    camera.host,
                    camera.port,
                    camera.username,
                    camera.password,
                    camera.path,
                    camera.transport,
                    camera.analytics_fps,
                    now,
                    now,
                ),
            )

    def delete_camera(self, camera_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))

    def list_monitors(self) -> list[MonitorConfig]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM monitors ORDER BY name").fetchall()
        return [monitor_from_row(row) for row in rows]

    def get_monitor(self, monitor_id: str) -> MonitorConfig | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,)).fetchone()
        return monitor_from_row(row) if row else None

    def save_monitor(self, monitor: MonitorConfig) -> None:
        now = utc_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO monitors
                  (id, name, model_id, camera_id, enabled, config_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name = excluded.name,
                  model_id = excluded.model_id,
                  camera_id = excluded.camera_id,
                  enabled = excluded.enabled,
                  config_json = excluded.config_json,
                  updated_at = excluded.updated_at
                """,
                (
                    monitor.id,
                    monitor.name,
                    monitor.model_id,
                    monitor.camera_id,
                    1 if monitor.enabled else 0,
                    json.dumps(monitor.config),
                    now,
                    now,
                ),
            )

    def delete_monitor(self, monitor_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))

    def record_observation(self, observation: Observation) -> None:
        if observation.monitor_id is None:
            return
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO monitor_states
                  (monitor_id, metric, value, value_json, observed_at, labels_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(monitor_id, metric) DO UPDATE SET
                  value = excluded.value,
                  value_json = excluded.value_json,
                  observed_at = excluded.observed_at,
                  labels_json = excluded.labels_json
                """,
                (
                    observation.monitor_id,
                    observation.metric,
                    str(observation.value),
                    json.dumps(observation.value),
                    observation.timestamp.isoformat(),
                    json.dumps(observation.labels),
                ),
            )

    def list_monitor_states(self) -> list[sqlite3.Row]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT monitors.name AS monitor_name, monitors.model_id, monitor_states.*
                FROM monitor_states
                JOIN monitors ON monitors.id = monitor_states.monitor_id
                ORDER BY monitor_states.observed_at DESC, monitors.name, monitor_states.metric
                """
            ).fetchall()

    def save_monitor_debug_snapshot(self, monitor_id: str, path: Path, observed_at: datetime) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO monitor_debug_snapshots (monitor_id, path, mime_type, observed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(monitor_id) DO UPDATE SET
                  path = excluded.path,
                  mime_type = excluded.mime_type,
                  observed_at = excluded.observed_at
                """,
                (monitor_id, str(path), "image/jpeg", observed_at.isoformat()),
            )

    def list_monitor_debug_snapshots(self) -> list[sqlite3.Row]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT monitors.name AS monitor_name, monitor_debug_snapshots.*
                FROM monitor_debug_snapshots
                JOIN monitors ON monitors.id = monitor_debug_snapshots.monitor_id
                ORDER BY monitor_debug_snapshots.observed_at DESC, monitors.name
                """
            ).fetchall()

    def get_monitor_debug_snapshot(self, monitor_id: str) -> sqlite3.Row | None:
        with self.db.connect() as conn:
            return conn.execute(
                "SELECT * FROM monitor_debug_snapshots WHERE monitor_id = ?",
                (monitor_id,),
            ).fetchone()

    def live_signatures(self) -> dict[str, str]:
        with self.db.connect() as conn:
            monitor_outputs = conn.execute(
                "SELECT COALESCE(MAX(observed_at), '') FROM monitor_states"
            ).fetchone()[0]
            monitor_debug = conn.execute(
                "SELECT COALESCE(MAX(observed_at), '') FROM monitor_debug_snapshots"
            ).fetchone()[0]
            events = conn.execute(
                "SELECT COALESCE(MAX(created_at), '') || ':' || COUNT(*) FROM events"
            ).fetchone()[0]
            relays = conn.execute(
                "SELECT COALESCE(MAX(updated_at), '') FROM relay_channels"
            ).fetchone()[0]
            cameras = conn.execute(
                "SELECT COALESCE(MAX(updated_at), '') FROM cameras"
            ).fetchone()[0]
        return {
            "monitor_outputs": str(monitor_outputs),
            "monitor_debug": str(monitor_debug),
            "events": str(events),
            "relays": str(relays),
            "health": "|".join(str(value) for value in [monitor_outputs, monitor_debug, events, relays, cameras]),
        }

    def set_camera_health(self, camera_id: str, health: str, last_error: str | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE cameras SET health = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (health, last_error, utc_iso(), camera_id),
            )

    def list_relays(self) -> list[RelayChannelConfig]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM relay_channels ORDER BY channel_number").fetchall()
        return [relay_from_row(row) for row in rows]

    def list_relay_rows(self) -> list[sqlite3.Row]:
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM relay_channels ORDER BY channel_number").fetchall()

    def get_relay(self, relay_id: str) -> RelayChannelConfig | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM relay_channels WHERE id = ?", (relay_id,)).fetchone()
        return relay_from_row(row) if row else None

    def update_relay_state(self, relay_id: str, state: RelayDesiredState) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE relay_channels SET current_state = ?, updated_at = ? WHERE id = ?",
                (state.value, utc_iso(), relay_id),
            )

    def list_rules(self) -> list[RuleConfig]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM rules ORDER BY priority DESC, name").fetchall()
        return [rule_from_row(row) for row in rows]

    def get_rule(self, rule_id: str) -> RuleConfig | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        return rule_from_row(row) if row else None

    def save_rule(self, rule: RuleConfig) -> None:
        now = utc_iso()
        condition = asdict(rule.condition)
        condition_group = asdict(rule.condition_group)
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO rules
                  (id, name, enabled, priority, monitor_id, camera_ids_json, plugin_id, condition_group_json, condition_json,
                   true_actions_json, false_actions_json, fault_actions_json,
                   debounce_true_ms, debounce_false_ms, cooldown_ms, fail_policy, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name = excluded.name,
                  enabled = excluded.enabled,
                  priority = excluded.priority,
                  monitor_id = excluded.monitor_id,
                  camera_ids_json = excluded.camera_ids_json,
                  plugin_id = excluded.plugin_id,
                  condition_group_json = excluded.condition_group_json,
                  condition_json = excluded.condition_json,
                  true_actions_json = excluded.true_actions_json,
                  false_actions_json = excluded.false_actions_json,
                  fault_actions_json = excluded.fault_actions_json,
                  debounce_true_ms = excluded.debounce_true_ms,
                  debounce_false_ms = excluded.debounce_false_ms,
                  cooldown_ms = excluded.cooldown_ms,
                  fail_policy = excluded.fail_policy,
                  updated_at = excluded.updated_at
                """,
                (
                    rule.id,
                    rule.name,
                    1 if rule.enabled else 0,
                    rule.priority,
                    rule.monitor_id,
                    json.dumps(rule.camera_ids),
                    rule.plugin_id,
                    json.dumps(condition_group),
                    json.dumps(condition),
                    json.dumps([action_to_json(action) for action in rule.true_actions]),
                    json.dumps([action_to_json(action) for action in rule.false_actions]),
                    json.dumps([action_to_json(action) for action in rule.fault_actions]),
                    rule.debounce_true_ms,
                    rule.debounce_false_ms,
                    rule.cooldown_ms,
                    rule.fail_policy.value,
                    now,
                    now,
                ),
            )

    def delete_rule(self, rule_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))

    def update_rule_runtime(
        self,
        rule_id: str,
        state: RuleState,
        last_observation_value: object | None,
        last_dedupe_key: str | None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE rules
                SET state = ?, last_observation_value = ?, last_dedupe_key = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    state.value,
                    "" if last_observation_value is None else str(last_observation_value),
                    last_dedupe_key,
                    utc_iso(),
                    rule_id,
                ),
            )

    def get_rule_row(self, rule_id: str) -> sqlite3.Row | None:
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()

    def create_event(
        self,
        *,
        event_id: str,
        camera_id: str,
        rule_id: str,
        state: RuleState,
        metric: str,
        value: object,
        observation_json: dict[str, Any],
        snapshot: SnapshotRef | None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO events
                  (id, camera_id, rule_id, state, metric, value, observation_json,
                   snapshot_path, snapshot_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    camera_id,
                    rule_id,
                    state.value,
                    metric,
                    str(value),
                    json.dumps(observation_json),
                    snapshot.path if snapshot else None,
                    snapshot.sha256 if snapshot else None,
                    utc_iso(),
                ),
            )

    def list_events(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    def get_event(self, event_id: str) -> sqlite3.Row | None:
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()

    def update_event_webhook_status(self, event_id: str, status: str) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE events SET webhook_status = ? WHERE id = ?", (status, event_id))

    def get_setting(self, key: str, default: str = "") -> str:
        with self.db.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def settings_dict(self) -> dict[str, str]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
        return {row["key"]: row["value"] for row in rows}


def hash_password(
    password: str,
    *,
    salt: str | None = None,
    iterations: int = 260_000,
) -> dict[str, object]:
    raw_salt = base64.b64decode(salt) if salt else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), raw_salt, iterations)
    return {
        "salt": base64.b64encode(raw_salt).decode("ascii"),
        "iterations": iterations,
        "digest": base64.b64encode(digest).decode("ascii"),
    }


def camera_from_row(row: sqlite3.Row) -> CameraConfig:
    return CameraConfig(
        id=row["id"],
        name=row["name"],
        host=row["host"],
        port=int(row["port"]),
        path=row["path"],
        username=row["username"],
        password=row["password"],
        transport=row["transport"],
        analytics_fps=float(row["analytics_fps"]),
    )


def relay_from_row(row: sqlite3.Row) -> RelayChannelConfig:
    return RelayChannelConfig(
        id=row["id"],
        board_id=row["board_id"],
        channel_number=int(row["channel_number"]),
        name=row["name"],
        default_state=RelayDesiredState(row["default_state"]),
        global_fail_state=FailPolicy(row["global_fail_state"]),
    )


def monitor_from_row(row: sqlite3.Row) -> MonitorConfig:
    return MonitorConfig(
        id=row["id"],
        name=row["name"],
        model_id=row["model_id"],
        camera_id=row["camera_id"],
        enabled=bool(row["enabled"]),
        config=json.loads(row["config_json"]),
    )


def rule_from_row(row: sqlite3.Row) -> RuleConfig:
    condition_payload = json.loads(row["condition_json"])
    group_payload = json.loads(row["condition_group_json"]) if row["condition_group_json"] else {
        "mode": "all",
        "conditions": [condition_payload],
    }
    conditions = [RuleCondition(**condition) for condition in group_payload["conditions"]]
    monitor_id = row["monitor_id"] or first_monitor_id_from_legacy(row)
    return RuleConfig(
        id=row["id"],
        name=row["name"],
        enabled=bool(row["enabled"]),
        priority=int(row["priority"]),
        monitor_id=monitor_id,
        condition_group=ConditionGroup(mode=group_payload["mode"], conditions=conditions),
        true_actions=[action_from_json(item) for item in json.loads(row["true_actions_json"])],
        false_actions=[action_from_json(item) for item in json.loads(row["false_actions_json"])],
        fault_actions=[action_from_json(item) for item in json.loads(row["fault_actions_json"])],
        debounce_true_ms=int(row["debounce_true_ms"]),
        debounce_false_ms=int(row["debounce_false_ms"]),
        cooldown_ms=int(row["cooldown_ms"]),
        fail_policy=FailPolicy(row["fail_policy"]),
    )


def first_monitor_id_from_legacy(row: sqlite3.Row) -> str:
    camera_ids = list(json.loads(row["camera_ids_json"]))
    return camera_ids[0] if camera_ids else ""


def action_to_json(action: Action) -> dict[str, object]:
    if isinstance(action, RelayAction):
        return {
            "type": "relay",
            "relay_channel_id": action.relay_channel_id,
            "desired_state": action.desired_state.value,
        }
    if isinstance(action, WebhookAction):
        return {
            "type": "webhook",
            "url": action.url,
            "snapshot_delivery": action.snapshot_delivery.value,
            "timeout_ms": action.timeout_ms,
            "retry_count": action.retry_count,
            "headers": action.headers,
            "hmac_secret": action.hmac_secret,
        }
    raise TypeError(f"unsupported action: {action}")


def action_from_json(payload: dict[str, object]) -> Action:
    if payload["type"] == "relay":
        return RelayAction(
            relay_channel_id=str(payload["relay_channel_id"]),
            desired_state=RelayDesiredState(str(payload["desired_state"])),
        )
    if payload["type"] == "webhook":
        return WebhookAction(
            url=str(payload["url"]),
            snapshot_delivery=SnapshotDelivery(str(payload.get("snapshot_delivery", SnapshotDelivery.MULTIPART.value))),
            timeout_ms=int(payload.get("timeout_ms", 5000)),
            retry_count=int(payload.get("retry_count", 2)),
            headers=dict(payload.get("headers", {})),
            hmac_secret=str(payload["hmac_secret"]) if payload.get("hmac_secret") else None,
        )
    raise ValueError(f"unsupported action type: {payload['type']}")
