from backend.app.db import Database, Repository
from backend.app.domain import CameraConfig, RelayAction, RelayDesiredState, RuleCondition, RuleConfig
from backend.app.storage.snapshots import SnapshotStore, StorageConfig
from backend.app.worker import MockInferenceProvider, Worker


def test_worker_creates_snapshot_event_and_updates_relay(tmp_path) -> None:
    db = Database(tmp_path / "smartai.db")
    repo = Repository(db)
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_rule(
        RuleConfig(
            id="rule-1",
            name="Mantrap",
            enabled=True,
            priority=100,
            camera_ids=["cam-1"],
            plugin_id="core.object_count",
            condition=RuleCondition("person.count", ">=", 2),
            true_actions=[RelayAction("relay-1", RelayDesiredState.ON)],
        )
    )
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        MockInferenceProvider({"cam-1": 2}),
    )

    worker.process_once()

    events = repo.list_events()

    assert len(events) == 1
    assert events[0]["metric"] == "person.count"
    assert events[0]["value"] == "2"
    assert events[0]["snapshot_path"]
    with db.connect() as conn:
        row = conn.execute("SELECT current_state FROM relay_channels WHERE id = 'relay-1'").fetchone()
    assert row["current_state"] == "on"


def test_worker_dedupes_unchanged_rule_event(tmp_path) -> None:
    db = Database(tmp_path / "smartai.db")
    repo = Repository(db)
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    repo.save_camera(camera)
    repo.save_rule(
        RuleConfig(
            id="rule-1",
            name="Mantrap",
            enabled=True,
            priority=100,
            camera_ids=["cam-1"],
            plugin_id="core.object_count",
            condition=RuleCondition("person.count", ">=", 2),
        )
    )
    worker = Worker(
        repo,
        SnapshotStore(StorageConfig(tmp_path / "snapshots", max_bytes=1024 * 1024, min_free_disk_percent=0)),
        MockInferenceProvider({"cam-1": 2}),
    )

    worker.process_once()
    worker.process_once()

    assert len(repo.list_events()) == 1
