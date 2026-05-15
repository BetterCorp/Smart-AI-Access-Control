from backend.app.domain import CameraConfig, Detection, MonitorConfig
import pytest

from backend.app.inference.hailo import observations_for, resolve_detection_resources


def test_hailo_observations_include_monitor_identity() -> None:
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    monitor = MonitorConfig(
        "mon-1",
        "Entrance people",
        "person_counter",
        "cam-1",
        config={"class_name": "person", "confidence_threshold": 0.5},
    )

    observations = observations_for(
        monitor,
        camera,
        [
            Detection("person", 0.9, (0.1, 0.1, 0.2, 0.5)),
            Detection("person", 0.4, (0.2, 0.1, 0.2, 0.5)),
        ],
    )

    assert [(item.metric, item.value) for item in observations] == [
        ("person.count", 1),
        ("person.present", True),
    ]
    assert all(item.monitor_id == "mon-1" for item in observations)
    assert all(item.model_id == "person_counter" for item in observations)


def test_hailo_resources_fail_cleanly_when_default_model_is_missing() -> None:
    with pytest.raises(RuntimeError, match="No default Hailo detection model"):
        resolve_detection_resources(
            {
                "load_environment": lambda _path: None,
                "DEFAULT_DOTENV_PATH": "/tmp/.env",
                "detect_hailo_arch": lambda: "hailo8l",
                "resolve_hef_path": lambda *_args, **_kwargs: None,
                "DETECTION_PIPELINE": "detection",
            }
        )
