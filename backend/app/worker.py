from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from backend.app.camera.snapshot import capture_rtsp_jpeg
from backend.app.config import load_settings
from backend.app.db import Database, Repository
from backend.app.domain import (
    Action,
    CameraConfig,
    Detection,
    InferenceResult,
    MonitorConfig,
    Observation,
    RelayAction,
    RelayDesiredState,
    RuleConfig,
    RuleState,
    SnapshotDelivery,
    WebhookAction,
)
from backend.app.inference.hailo import HailoGStreamerProvider
from backend.app.plugins.object_count import ObjectCountConfig, ObjectCountPlugin
from backend.app.relay.service import RelayArbiter, RelayCommand, RelayConflictError, UsbRelayDriver
from backend.app.rules.engine import RuleEngine
from backend.app.storage.snapshots import SnapshotStore, StorageConfig, event_id, snapshot_dedupe_key
from backend.app.system_metrics import hailo_telemetry_is_active
from backend.app.webhooks.service import PreparedWebhook, WebhookBuilder


ONE_PIXEL_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010101006000600000ffdb0043000302020302020303030304030304050805050404050a070706080c0a0c0c0b0a0b0b0d0e12100d0e110e0b0b1016101113141515150c0f171816141812141514ffdb00430103040405040509050509140d0b0d14141414141414141414141414141414141414141414141414141414141414141414141414141414141414141414141414141414ffc00011080001000103012200021101031101ffc4001400010000000000000000000000000000000000000008ffc40014100100000000000000000000000000000000000000ffda000c03010002110311003f00b2c001ffd9"
)


class InferenceProvider(Protocol):
    def result_for(self, monitor: MonitorConfig, camera: CameraConfig) -> InferenceResult:
        ...


@dataclass
class MockInferenceProvider:
    counts: dict[str, int]

    def result_for(self, monitor: MonitorConfig, camera: CameraConfig) -> InferenceResult:
        count = self.counts.get(monitor.id, self.counts.get(camera.id, 0))
        class_name = str(monitor.config.get("class_name", "person"))
        confidence = float(monitor.config.get("confidence_threshold", 0.5))
        detections = [Detection(class_name, 0.9, (0.1 + index * 0.05, 0.1, 0.2, 0.5)) for index in range(max(0, count))]
        from backend.app.inference.hailo import monitor_zone

        observations = ObjectCountPlugin().on_detections(
            camera,
            detections,
            ObjectCountConfig(
                class_name,
                confidence,
                zone_id=str(monitor.config.get("zone_id")) if monitor.config.get("zone_id") else None,
                zone=monitor_zone(monitor),
            ),
        )
        return InferenceResult(
            [
                Observation(
                    observation.plugin_id,
                    observation.camera_id,
                    observation.metric,
                    observation.value,
                    timestamp=observation.timestamp,
                    monitor_id=monitor.id,
                    model_id=monitor.model_id,
                    zone_id=observation.zone_id,
                    labels=observation.labels,
                    metadata=observation.metadata,
                )
                for observation in observations
            ]
        )


class Worker:
    def __init__(
        self,
        repo: Repository,
        snapshot_store: SnapshotStore,
        inference: InferenceProvider,
        relay_driver: UsbRelayDriver | None = None,
        inference_mode: str = "unknown",
        relay_hardware_enabled: bool | None = None,
    ) -> None:
        self.repo = repo
        self.snapshot_store = snapshot_store
        self.inference = inference
        self.relay_driver = relay_driver
        self.rule_engine = RuleEngine()
        self.webhooks = WebhookBuilder()
        self.relay_arbiter = RelayArbiter()
        self.inference_mode = inference_mode
        self.relay_hardware_enabled = relay_driver is not None if relay_hardware_enabled is None else relay_hardware_enabled
        self.relay_probe = relay_driver or UsbRelayDriver()
        self._hailo_telemetry_enabled: bool | None = None

    def run_forever(self, interval_seconds: float = 1.0) -> None:
        while True:
            self.process_once()
            time.sleep(interval_seconds)

    def process_once(self) -> None:
        self.repo.update_worker_status(
            self.inference_mode,
            self.relay_hardware_enabled,
            self.relay_probe.device_count(),
        )
        cameras = self.repo.list_cameras()
        camera_by_id = {camera.id: camera for camera in cameras}
        monitors = self.repo.list_monitors()
        self._sync_hailo_telemetry_state()
        prune_sessions = getattr(self.inference, "prune_sessions", None)
        if callable(prune_sessions):
            prune_sessions(monitors, camera_by_id)
        observations_by_monitor: dict[str, list[Observation]] = {}
        fallback_debug_by_camera: dict[str, bytes | None] = {}

        for monitor in monitors:
            camera = camera_by_id.get(monitor.camera_id)
            if camera is None or not monitor.enabled:
                observations_by_monitor[monitor.id] = []
                self.repo.update_monitor_runtime(
                    monitor.id,
                    "disabled" if not monitor.enabled else "camera_missing",
                )
                continue
            try:
                result = self.inference.result_for(monitor, camera)
                observations = result.observations
                observations_by_monitor[monitor.id] = observations
                for observation in observations:
                    self.repo.record_observation(observation)
                debug_jpeg = result.debug_jpeg
                if debug_jpeg is None:
                    if camera.id not in fallback_debug_by_camera:
                        fallback_debug_by_camera[camera.id] = capture_rtsp_jpeg(camera)
                    debug_jpeg = fallback_debug_by_camera[camera.id]
                if debug_jpeg is not None:
                    path = self.snapshot_store.write_monitor_debug_snapshot(monitor.id, debug_jpeg)
                    self.repo.save_monitor_debug_snapshot(monitor.id, path, datetime.now(timezone.utc))
                self.repo.update_monitor_runtime(
                    monitor.id,
                    "online",
                    last_success_at=datetime.now(timezone.utc).isoformat(),
                )
                self.repo.set_camera_health(camera.id, "online")
            except Exception as exc:
                observations_by_monitor[monitor.id] = []
                error = str(exc)
                status = "waiting" if error.startswith("Waiting for first Hailo frame") else "error"
                self.repo.update_monitor_runtime(monitor.id, status, error)
                self.repo.set_camera_health(camera.id, "stream_error", error)

        relay_commands: list[RelayCommand] = []
        for rule in self.repo.list_rules():
            observations = observations_by_monitor.get(rule.monitor_id, [])
            faulted = rule.monitor_id not in observations_by_monitor or not observations
            evaluation = self.rule_engine.evaluate(rule, observations, datetime.now(timezone.utc), faulted=faulted)
            event_actions = evaluation.actions

            if evaluation.matched_observation is not None and evaluation.state in {RuleState.TRUE, RuleState.FALSE}:
                event_actions = self._persist_event_if_changed(rule, evaluation.state, evaluation.matched_observation, event_actions)

            relay_commands.extend(self._relay_commands(rule, event_actions))
            self._dispatch_webhooks(rule, camera_by_id, evaluation.state, evaluation.matched_observation, event_actions)

        self._apply_relays(relay_commands)
        self.snapshot_store.prune()

    def _sync_hailo_telemetry_state(self) -> None:
        set_telemetry_enabled = getattr(self.inference, "set_telemetry_enabled", None)
        if not callable(set_telemetry_enabled):
            return
        monitor_support_enabled = os.environ.get("SMARTAI_ENABLE_HAILO_MONITOR", "1") == "1"
        telemetry_enabled = monitor_support_enabled and hailo_telemetry_is_active(load_settings().data_dir)
        if telemetry_enabled == self._hailo_telemetry_enabled:
            return
        set_telemetry_enabled(telemetry_enabled)
        self._hailo_telemetry_enabled = telemetry_enabled

    def _persist_event_if_changed(
        self,
        rule: RuleConfig,
        state: RuleState,
        observation: Observation,
        actions: list[Action],
    ) -> list[Action]:
        row = self.repo.get_rule_row(rule.id)
        previous_key = row["last_dedupe_key"] if row else None
        dedupe_key = snapshot_dedupe_key(
            rule.id,
            observation.camera_id,
            state.value,
            observation.metric,
            observation.value,
            observation.zone_id,
        )
        if not self.snapshot_store.should_store(dedupe_key, previous_key, min_interval_elapsed=True):
            self.repo.update_rule_runtime(rule.id, state, observation.value, previous_key)
            return actions

        eid = event_id()
        snapshot = self.snapshot_store.write_snapshot(eid, self._snapshot_bytes_for(observation))
        self.repo.create_event(
            event_id=eid,
            camera_id=observation.camera_id,
            rule_id=rule.id,
            state=state,
            metric=observation.metric,
            value=observation.value,
            observation_json=observation_to_json(observation),
            snapshot=snapshot,
        )
        self.repo.update_rule_runtime(rule.id, state, observation.value, dedupe_key)
        return actions

    def _snapshot_bytes_for(self, observation: Observation) -> bytes:
        if observation.monitor_id is not None:
            row = self.repo.get_monitor_debug_snapshot(observation.monitor_id)
            if row is not None:
                path = Path(row["path"])
                if path.exists():
                    return path.read_bytes()
        return ONE_PIXEL_JPEG

    def _relay_commands(self, rule: RuleConfig, actions: list[Action]) -> list[RelayCommand]:
        commands = []
        for action in actions:
            if isinstance(action, RelayAction):
                commands.append(
                    RelayCommand(
                        channel_id=action.relay_channel_id,
                        state=action.desired_state,
                        source_rule_id=rule.id,
                        priority=rule.priority,
                    )
                )
        return commands

    def _dispatch_webhooks(
        self,
        rule: RuleConfig,
        camera_by_id: dict[str, CameraConfig],
        state: RuleState,
        observation: Observation | None,
        actions: list[Action],
    ) -> None:
        if observation is None:
            return
        event = latest_event_for(self.repo, rule.id, observation.camera_id)
        snapshot_bytes = None
        snapshot_ref = None
        if event and event["snapshot_path"]:
            path = Path(event["snapshot_path"])
            if path.exists():
                snapshot_bytes = path.read_bytes()
                snapshot_ref = snapshot_ref_from_event(event)

        for action in actions:
            if not isinstance(action, WebhookAction):
                continue
            prepared = self.webhooks.prepare(
                action,
                event_id=event["id"] if event else event_id(),
                camera={
                    "id": observation.camera_id,
                    "name": camera_by_id.get(observation.camera_id, CameraConfig(observation.camera_id, observation.camera_id, "", 0, "")).name,
                },
                rule={"id": rule.id, "name": rule.name, "state": state.value},
                observation=observation,
                snapshot=snapshot_ref,
                snapshot_bytes=snapshot_bytes,
            )
            status = send_webhook(prepared)
            if event:
                self.repo.update_event_webhook_status(event["id"], status)

    def _apply_relays(self, commands: list[RelayCommand]) -> None:
        try:
            states = self.relay_arbiter.resolve(commands)
        except RelayConflictError:
            return

        relays = {relay.id: relay for relay in self.repo.list_relays()}
        for relay_id, state in states.items():
            relay = relays.get(relay_id)
            if relay is None:
                continue
            if self.relay_driver is not None:
                try:
                    self.relay_driver.set_channel(relay.board_id, relay.channel_number, state)
                except Exception:
                    continue
            self.repo.update_relay_state(relay_id, state)


def latest_event_for(repo: Repository, rule_id: str, camera_id: str):
    for event in repo.list_events(limit=50):
        if event["rule_id"] == rule_id and event["camera_id"] == camera_id:
            return event
    return None


def snapshot_ref_from_event(event) -> object:
    from backend.app.domain import SnapshotRef

    path = Path(event["snapshot_path"])
    return SnapshotRef(
        event_id=event["id"],
        path=str(path),
        mime_type="image/jpeg",
        filename=path.name,
        sha256=event["snapshot_sha256"] or "",
        bytes_len=path.stat().st_size if path.exists() else 0,
    )


def observation_to_json(observation: Observation) -> dict[str, object]:
    return {
        "pluginId": observation.plugin_id,
        "modelId": observation.model_id,
        "monitorId": observation.monitor_id,
        "cameraId": observation.camera_id,
        "metric": observation.metric,
        "value": observation.value,
        "zoneId": observation.zone_id,
        "labels": observation.labels,
        "metadata": observation.metadata,
        "timestamp": observation.timestamp.isoformat(),
    }


def send_webhook(prepared: PreparedWebhook) -> str:
    request = urllib.request.Request(prepared.url, data=prepared.body, headers=prepared.headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=prepared.timeout_ms / 1000) as response:
            return f"sent:{response.status}"
    except urllib.error.URLError as exc:
        return f"failed:{exc.reason}"
    except Exception as exc:
        return f"failed:{exc}"


def mock_counts_from_settings(repo: Repository) -> dict[str, int]:
    raw = repo.get_setting("mock_person_counts", "{}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {str(key): int(value) for key, value in payload.items()}


def build_worker() -> Worker:
    settings = load_settings()
    repository = Repository(Database(settings.db_path))
    snapshot_store = SnapshotStore(
        StorageConfig(
            snapshot_root=settings.snapshot_root,
            max_bytes=int(repository.get_setting("snapshot_max_bytes", str(10 * 1024 * 1024 * 1024))),
            min_free_disk_percent=float(repository.get_setting("snapshot_min_free_disk_percent", "20")),
        )
    )
    inference: InferenceProvider
    if settings.use_mock_inference:
        inference = MockInferenceProvider(mock_counts_from_settings(repository))
        inference_mode = "mock"
    else:
        inference = HailoGStreamerProvider()
        inference_mode = "real"
    relay_driver = UsbRelayDriver() if settings.enable_relay_hardware else None
    return Worker(
        repository,
        snapshot_store,
        inference,
        relay_driver,
        inference_mode=inference_mode,
        relay_hardware_enabled=settings.enable_relay_hardware,
    )


def main() -> None:
    build_worker().run_forever()


if __name__ == "__main__":
    main()
