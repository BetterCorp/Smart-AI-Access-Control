from backend.app.domain import CameraConfig, Detection
from backend.app.plugins.object_count import ObjectCountConfig, ObjectCountPlugin


def test_object_count_filters_detections_by_zone_and_exports_metadata() -> None:
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    observations = ObjectCountPlugin().on_detections(
        camera,
        [
            Detection("person", 0.9, (0.1, 0.1, 0.2, 0.2)),
            Detection("person", 0.9, (0.7, 0.7, 0.2, 0.2)),
        ],
        ObjectCountConfig("person", 0.5, zone_id="left", zone=(0, 0, 0.5, 0.5)),
    )

    assert [(item.metric, item.value) for item in observations] == [
        ("person.count", 1),
        ("person.present", True),
    ]
    assert observations[0].zone_id == "left"
    assert observations[0].metadata["zone"] == {
        "id": "left",
        "x": 0,
        "y": 0,
        "width": 0.5,
        "height": 0.5,
    }
    assert len(observations[0].metadata["detections"]) == 1
