from backend.app.domain import CameraConfig, Detection, MonitorConfig
import pytest

from backend.app.inference.hailo import (
    HailoMonitorSession,
    HailoPipelineResources,
    build_detection_pipeline,
    format_child_exit,
    observations_for,
    resolve_detection_resources,
)


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


def test_hailo_session_reports_first_frame_timeout() -> None:
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    monitor = MonitorConfig("mon-1", "Entrance people", "person_counter", "cam-1")
    session = HailoMonitorSession(monitor, camera, fingerprint=())
    session._started_at -= 10
    session._queue.put_nowait(("bus", "Pipeline state changed from ready to paused."))

    with pytest.raises(RuntimeError, match="No Hailo frame received after 10s"):
        session.latest_result()


def test_hailo_pipeline_keeps_callback_before_headless_sink() -> None:
    bindings = {
        "SOURCE_PIPELINE": lambda *_args, **_kwargs: "source",
        "INFERENCE_PIPELINE": lambda **_kwargs: "inference",
        "INFERENCE_PIPELINE_WRAPPER": lambda inner: f"wrapped({inner})",
        "TRACKER_PIPELINE": lambda class_id: f"tracker({class_id})",
        "USER_CALLBACK_PIPELINE": lambda: "identity name=identity_callback",
    }

    pipeline = build_detection_pipeline(
        bindings,
        "rtsp://camera/live",
        HailoPipelineResources("/tmp/model.hef", "/tmp/post.so", "filter", None),
        analytics_fps=2,
    )

    assert "identity name=identity_callback ! videoconvert" in pipeline
    assert "hailooverlay" not in pipeline


def test_hailo_child_signal_exit_is_reported() -> None:
    assert format_child_exit(-11) == "Hailo pipeline process exited with SIGSEGV."
