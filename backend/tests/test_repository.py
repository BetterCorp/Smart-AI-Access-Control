from backend.app.db import Database, Repository
from datetime import datetime, timezone

from backend.app.domain import CameraConfig, ConditionGroup, MonitorConfig, Observation, RelayAction, RelayDesiredState, RuleCondition, RuleConfig


def test_repository_persists_camera_and_rule_with_actions(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live", "admin", "secret")
    monitor = MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1")
    rule = RuleConfig(
        id="rule-1",
        name="Mantrap",
        enabled=True,
        priority=100,
        monitor_id="mon-1",
        condition_group=ConditionGroup("all", [RuleCondition("person.count", ">=", 2)]),
        true_actions=[RelayAction("relay-1", RelayDesiredState.ON)],
        false_actions=[RelayAction("relay-1", RelayDesiredState.OFF)],
    )

    repo.save_camera(camera)
    repo.save_monitor(monitor)
    repo.save_rule(rule)

    loaded_camera = repo.get_camera("cam-1")
    loaded_monitor = repo.get_monitor("mon-1")
    loaded_rule = repo.get_rule("rule-1")

    assert loaded_camera == camera
    assert loaded_monitor == monitor
    assert loaded_rule == rule


def test_repository_updates_monitor_in_place(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    repo.save_camera(CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live"))
    repo.save_camera(CameraConfig("cam-2", "Exit", "192.168.1.51", 554, "/live"))
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1"))

    repo.save_monitor(
        MonitorConfig(
            "mon-1",
            "Exit cars",
            "object_detector",
            "cam-2",
            config={"class_name": "car", "confidence_threshold": 0.7},
        )
    )

    assert repo.get_monitor("mon-1") == MonitorConfig(
        "mon-1",
        "Exit cars",
        "object_detector",
        "cam-2",
        config={"class_name": "car", "confidence_threshold": 0.7},
    )


def test_live_signatures_change_when_monitor_output_changes(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    repo.save_camera(CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live"))
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1"))
    before = repo.live_signatures()

    repo.record_observation(
        Observation(
            "core.object_count",
            "cam-1",
            "person.count",
            2,
            timestamp=datetime(2026, 5, 15, tzinfo=timezone.utc),
            monitor_id="mon-1",
            model_id="object_detector",
        )
    )
    after = repo.live_signatures()

    assert before["monitor_outputs"] != after["monitor_outputs"]
    assert before["health"] != after["health"]


def test_worker_status_is_persisted_and_changes_health_signature(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    before = repo.live_signatures()

    repo.update_worker_status("real", relay_hardware_enabled=False, relay_device_count=4)
    status = repo.get_worker_status()
    after = repo.live_signatures()

    assert status is not None
    assert status["inference_mode"] == "real"
    assert status["relay_hardware_enabled"] == 0
    assert status["relay_device_count"] == 4
    assert before["health"] != after["health"]


def test_camera_health_only_updates_when_state_changes(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    repo.save_camera(CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live"))

    repo.set_camera_health("cam-1", "online")
    first = repo.list_camera_rows()[0]
    repo.set_camera_health("cam-1", "online")
    unchanged = repo.list_camera_rows()[0]
    repo.set_camera_health("cam-1", "stream_error", "timeout")
    changed = repo.list_camera_rows()[0]

    assert unchanged["updated_at"] == first["updated_at"]
    assert changed["updated_at"] != unchanged["updated_at"]


def test_monitor_runtime_is_created_and_updated(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    repo.save_camera(CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live"))
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1"))

    rows = repo.list_monitor_runtime_rows()
    assert rows[0]["status"] == "configured"

    before = repo.live_signatures()
    repo.update_monitor_runtime("mon-1", "error", "stream failed")
    after = repo.live_signatures()

    rows = repo.list_monitor_runtime_rows()
    assert rows[0]["status"] == "error"
    assert rows[0]["last_error"] == "stream failed"
    assert before["monitors"] != after["monitors"]


def test_delete_monitor_cleans_runtime_state_and_debug_rows(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    repo.save_camera(CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live"))
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1"))
    debug_path = tmp_path / "snapshots" / "monitor-debug" / "mon-1.jpg"
    debug_path.parent.mkdir(parents=True)
    debug_path.write_bytes(b"debug")
    repo.save_monitor_debug_snapshot("mon-1", debug_path, datetime(2026, 5, 15, tzinfo=timezone.utc))
    repo.record_observation(
        Observation(
            "core.object_count",
            "cam-1",
            "person.count",
            1,
            timestamp=datetime(2026, 5, 15, tzinfo=timezone.utc),
            monitor_id="mon-1",
            model_id="object_detector",
        )
    )

    paths = repo.delete_monitor("mon-1")

    assert paths == [debug_path]
    assert repo.get_monitor("mon-1") is None
    assert repo.list_monitor_runtime_rows() == []
    assert repo.list_monitor_states() == []
    assert repo.get_monitor_debug_snapshot("mon-1") is None


def test_delete_camera_cleans_child_monitors(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    repo.save_camera(CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live"))
    repo.save_monitor(MonitorConfig("mon-1", "Entrance people", "object_detector", "cam-1"))
    debug_path = tmp_path / "snapshots" / "monitor-debug" / "mon-1.jpg"
    debug_path.parent.mkdir(parents=True)
    debug_path.write_bytes(b"debug")
    repo.save_monitor_debug_snapshot("mon-1", debug_path, datetime(2026, 5, 15, tzinfo=timezone.utc))

    paths = repo.delete_camera("cam-1")

    assert paths == [debug_path]
    assert repo.get_camera("cam-1") is None
    assert repo.get_monitor("mon-1") is None
    assert repo.get_monitor_debug_snapshot("mon-1") is None
