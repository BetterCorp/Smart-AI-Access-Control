from backend.app.db import Database, Repository
from backend.app.domain import CameraConfig, RelayAction, RelayDesiredState, RuleCondition, RuleConfig


def test_repository_persists_camera_and_rule_with_actions(tmp_path) -> None:
    repo = Repository(Database(tmp_path / "smartai.db"))
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live", "admin", "secret")
    rule = RuleConfig(
        id="rule-1",
        name="Mantrap",
        enabled=True,
        priority=100,
        camera_ids=["cam-1"],
        plugin_id="core.object_count",
        condition=RuleCondition("person.count", ">=", 2),
        true_actions=[RelayAction("relay-1", RelayDesiredState.ON)],
        false_actions=[RelayAction("relay-1", RelayDesiredState.OFF)],
    )

    repo.save_camera(camera)
    repo.save_rule(rule)

    loaded_camera = repo.get_camera("cam-1")
    loaded_rule = repo.get_rule("rule-1")

    assert loaded_camera == camera
    assert loaded_rule == rule

