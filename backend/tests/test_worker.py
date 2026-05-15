from backend.app.db import Database, Repository
from backend.app.domain import CameraConfig, ConditionGroup, InferenceResult, MonitorConfig, Observation, RelayAction, RelayDesiredState, RuleCondition, RuleConfig
from backend.app.storage.snapshots import SnapshotStore, StorageConfig
from backend.app.worker import MockInferenceProvider, Worker


def test_worker_creates_snapshot_event_and_updates_relay(tmp_path) -> None:
    db = Database(tmp_path / "smartai.db")
    repo = Repository(db)
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "person_counter", "cam-1"))
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
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "person_counter", "cam-1"))
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


def test_worker_evaluates_boolean_weapon_monitor(tmp_path) -> None:
    db = Database(tmp_path / "smartai.db")
    repo = Repository(db)
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-weapon", "Entrance weapons", "weapon_visibility", "cam-1"))
    repo.save_rule(
        RuleConfig(
            id="rule-weapon",
            name="Weapon visible",
            enabled=True,
            priority=100,
            monitor_id="mon-weapon",
            condition_group=ConditionGroup("all", [RuleCondition("weapon.visible", "is_true", True)]),
            true_actions=[RelayAction("relay-1", RelayDesiredState.ON)],
        )
    )
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        MockInferenceProvider({"mon-weapon": 1}),
    )

    worker.process_once()

    events = repo.list_events()
    assert len(events) == 1
    assert events[0]["metric"] == "weapon.visible"
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


def test_worker_stores_monitor_debug_snapshot(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "person_counter", "cam-1"))
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        DebugInferenceProvider(),
    )

    worker.process_once()

    rows = repo.list_monitor_debug_snapshots()
    assert len(rows) == 1
    assert rows[0]["monitor_id"] == "mon-1"


class FailingInferenceProvider:
    def result_for(self, monitor: MonitorConfig, camera: CameraConfig) -> InferenceResult:
        raise RuntimeError("Waiting for first Hailo frame from the RTSP pipeline (1s).")


def test_worker_records_waiting_monitor_runtime(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "person_counter", "cam-1"))
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        FailingInferenceProvider(),
    )

    worker.process_once()

    rows = repo.list_monitor_runtime_rows()
    assert rows[0]["status"] == "waiting"
    assert rows[0]["last_error"].startswith("Waiting for first Hailo frame")
