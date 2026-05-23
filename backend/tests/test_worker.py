from pathlib import Path

from backend.app.db import Database, Repository
from backend.app.domain import CameraConfig, ConditionGroup, InferenceResult, MonitorConfig, Observation, RelayAction, RelayDesiredState, RuleCondition, RuleConfig
from backend.app.storage.snapshots import SnapshotStore, StorageConfig
from backend.app.worker import MockInferenceProvider, Worker


def test_worker_creates_snapshot_event_and_updates_relay(tmp_path) -> None:
    db = Database(tmp_path / "smartai.db")
    repo = Repository(db)
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1"))
    repo.save_rule(
        RuleConfig(
            id="rule-1",
            name="Mantrap",
            enabled=True,
            priority=100,
            monitor_id="mon-1",
            condition_group=ConditionGroup("all", [RuleCondition("person.count", ">=", 2)]),
            true_actions=[RelayAction("relay-1", RelayDesiredState.ON)],
        )
    )
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        MockInferenceProvider({"mon-1": 2}),
        inference_mode="mock",
    )

    worker.process_once()

    events = repo.list_events()

    assert len(events) == 1
    assert events[0]["metric"] == "person.count"
    assert events[0]["value"] == "2"
    assert events[0]["snapshot_path"]
    states = repo.list_monitor_states()
    assert {state["metric"] for state in states} == {"person.count", "person.present"}
    assert repo.get_worker_status()["inference_mode"] == "mock"
    with db.connect() as conn:
        row = conn.execute("SELECT current_state FROM relay_channels WHERE id = 'relay-1'").fetchone()
    assert row["current_state"] == "on"


def test_worker_dedupes_unchanged_rule_event(tmp_path) -> None:
    db = Database(tmp_path / "smartai.db")
    repo = Repository(db)
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1"))
    repo.save_rule(
        RuleConfig(
            id="rule-1",
            name="Mantrap",
            enabled=True,
            priority=100,
            monitor_id="mon-1",
            condition_group=ConditionGroup("all", [RuleCondition("person.count", ">=", 2)]),
        )
    )
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        MockInferenceProvider({"mon-1": 2}),
    )

    worker.process_once()
    worker.process_once()

    assert len(repo.list_events()) == 1


def test_worker_evaluates_boolean_presence_monitor(tmp_path) -> None:
    db = Database(tmp_path / "smartai.db")
    repo = Repository(db)
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-present", "Entrance people", "object_detector", "cam-1"))
    repo.save_rule(
        RuleConfig(
            id="rule-present",
            name="People visible",
            enabled=True,
            priority=100,
            monitor_id="mon-present",
            condition_group=ConditionGroup("all", [RuleCondition("person.present", "is_true", True)]),
            true_actions=[RelayAction("relay-1", RelayDesiredState.ON)],
        )
    )
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        MockInferenceProvider({"mon-present": 1}),
    )

    worker.process_once()

    events = repo.list_events()
    assert len(events) == 1
    assert events[0]["metric"] == "person.present"
    with db.connect() as conn:
        row = conn.execute("SELECT current_state FROM relay_channels WHERE id = 'relay-1'").fetchone()
    assert row["current_state"] == "on"


class DebugInferenceProvider:
    def result_for(self, monitor: MonitorConfig, camera: CameraConfig) -> InferenceResult:
        return InferenceResult(
            [
                Observation(
                    "core.object_count",
                    camera.id,
                    "person.count",
                    2,
                    monitor_id=monitor.id,
                    model_id=monitor.model_id,
                )
            ],
            debug_jpeg=b"debug-jpeg",
        )


class BatchDebugInferenceProvider:
    def __init__(self) -> None:
        self.batch_calls = 0
        self.single_calls = 0

    def result_for(self, monitor: MonitorConfig, camera: CameraConfig) -> InferenceResult:
        self.single_calls += 1
        return InferenceResult([])

    def results_for_camera(self, monitors: list[MonitorConfig], camera: CameraConfig) -> dict[str, InferenceResult]:
        self.batch_calls += 1
        return {
            monitor.id: InferenceResult(
                [
                    Observation(
                        "core.object_count",
                        camera.id,
                        f"{monitor.config.get('class_name', 'person')}.count",
                        1,
                        monitor_id=monitor.id,
                        model_id=monitor.model_id,
                    )
                ],
                debug_jpeg=b"debug-jpeg",
            )
            for monitor in monitors
        }


def test_worker_batches_monitors_for_same_camera_when_provider_supports_it(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-person", "Person", "object_detector", "cam-1", config={"class_name": "person"}))
    repo.save_monitor(MonitorConfig("mon-backpack", "Backpack", "object_detector", "cam-1", config={"class_name": "backpack"}))
    inference = BatchDebugInferenceProvider()
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        inference,
    )

    worker.process_once()

    states = repo.list_monitor_states()
    assert inference.batch_calls == 1
    assert inference.single_calls == 0
    assert {(state["monitor_id"], state["metric"]) for state in states} == {
        ("mon-person", "person.count"),
        ("mon-backpack", "backpack.count"),
    }


def test_worker_stores_monitor_debug_snapshot(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1"))
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        DebugInferenceProvider(),
    )

    worker.process_once()

    rows = repo.list_monitor_debug_snapshots()
    assert len(rows) == 1
    assert rows[0]["monitor_id"] == "mon-1"


def test_worker_uses_monitor_debug_snapshot_for_rule_event(tmp_path) -> None:
    db = Database(tmp_path / "smartai.db")
    repo = Repository(db)
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1"))
    repo.save_rule(
        RuleConfig(
            id="rule-1",
            name="Mantrap",
            enabled=True,
            priority=100,
            monitor_id="mon-1",
            condition_group=ConditionGroup("all", [RuleCondition("person.count", ">=", 2)]),
        )
    )
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        DebugInferenceProvider(),
    )

    worker.process_once()

    event = repo.list_events()[0]
    assert event["snapshot_path"]
    assert Path(event["snapshot_path"]).read_bytes() == b"debug-jpeg"


class FailingInferenceProvider:
    def __init__(self) -> None:
        self.calls = 0

    def result_for(self, monitor: MonitorConfig, camera: CameraConfig) -> InferenceResult:
        self.calls += 1
        raise RuntimeError("Waiting for first Hailo frame from the RTSP pipeline (1s).")


def test_worker_records_waiting_monitor_runtime(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1"))
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        FailingInferenceProvider(),
    )

    worker.process_once()

    rows = repo.list_monitor_runtime_rows()
    assert rows[0]["status"] == "waiting"
    assert rows[0]["last_error"].startswith("Waiting for first Hailo frame")


def test_worker_reuses_camera_error_for_same_cycle(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-1", "Person", "object_detector", "cam-1"))
    repo.save_monitor(MonitorConfig("mon-2", "Backpack", "object_detector", "cam-1"))
    inference = FailingInferenceProvider()
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        inference,
    )

    worker.process_once()

    rows = repo.list_monitor_runtime_rows()
    assert inference.calls == 1
    assert {row["status"] for row in rows} == {"waiting"}
    assert all(row["last_error"].startswith("Waiting for first Hailo frame") for row in rows)


def test_worker_closes_inference_on_shutdown(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))

    class ClosingInferenceProvider:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    inference = ClosingInferenceProvider()
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        inference,
    )

    def stop_after_once() -> None:
        raise KeyboardInterrupt

    worker.process_once = stop_after_once

    try:
        worker.run_forever(interval_seconds=0)
    except KeyboardInterrupt:
        pass

    assert inference.closed is True


class FailingRelayDriver:
    def device_count(self) -> int:
        return 1

    def set_channel(self, board_id: str, channel_number: int, state: RelayDesiredState) -> None:
        raise TimeoutError("relay timeout")


def test_worker_keeps_running_when_relay_write_fails(tmp_path) -> None:
    db = Database(tmp_path / "smartai.db")
    repo = Repository(db)
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1"))
    repo.save_rule(
        RuleConfig(
            id="rule-1",
            name="Mantrap",
            enabled=True,
            priority=100,
            monitor_id="mon-1",
            condition_group=ConditionGroup("all", [RuleCondition("person.count", ">=", 2)]),
            true_actions=[RelayAction("relay-1", RelayDesiredState.ON)],
        )
    )
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        MockInferenceProvider({"mon-1": 2}),
        relay_driver=FailingRelayDriver(),
    )

    worker.process_once()

    with db.connect() as conn:
        row = conn.execute("SELECT current_state FROM relay_channels WHERE id = 'relay-1'").fetchone()
    assert row["current_state"] == "off"
