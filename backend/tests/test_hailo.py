from backend.app.domain import CameraConfig, Detection, MonitorConfig
import pytest

from backend.app.inference.hailo import (
    HailoCameraSession,
    HailoDetectionFrame,
    HailoGStreamerProvider,
    HailoPipelineRunner,
    HailoPipelineResources,
    build_detection_pipeline,
    build_rtsp_video_source_pipeline,
    format_child_exit,
    observations_for,
    ensure_readable_resource,
    resolve_detection_resources,
)


def test_hailo_observations_include_monitor_identity() -> None:
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    monitor = MonitorConfig(
        "mon-1",
        "Entrance people",
        "object_detector",
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
    assert all(item.model_id == "object_detector" for item in observations)


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


def test_hailo_resource_readability_is_checked(tmp_path) -> None:
    missing = tmp_path / "missing.hef"

    with pytest.raises(RuntimeError, match="Hailo detection model is missing"):
        ensure_readable_resource(missing, "Hailo detection model")


def test_hailo_session_reports_first_frame_timeout() -> None:
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    session = HailoCameraSession(camera, fingerprint=())
    session._started_at -= 10
    session._queue.put_nowait(("bus", "Pipeline state changed from ready to paused."))

    with pytest.raises(RuntimeError, match="No Hailo frame received after 10s"):
        session.latest_frame()


def test_hailo_pipeline_keeps_callback_before_headless_sink() -> None:
    bindings = {
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

    assert "video/x-raw,format=RGB ! identity name=identity_callback" in pipeline
    assert "hailooverlay" not in pipeline


def test_rtsp_source_pipeline_selects_video_media_only() -> None:
    pipeline = build_rtsp_video_source_pipeline("rtsp://camera/live", analytics_fps=2)

    assert 'rtspsrc location="rtsp://camera/live"' in pipeline
    assert "application/x-rtp,media=video" in pipeline
    assert 'caps="video/x-raw,framerate=2/1"' in pipeline


def test_hailo_child_signal_exit_is_reported() -> None:
    assert format_child_exit(-11) == "Hailo pipeline process exited with SIGSEGV."


def test_hailo_debug_snapshot_failure_does_not_drop_observations() -> None:
    published: list[tuple[str, object]] = []
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    runner = HailoPipelineRunner(camera, output_queue=None)
    runner._publish = lambda kind, payload: published.append((kind, payload))

    class FakeDetection:
        def get_bbox(self):
            return type("BBox", (), {"xmin": lambda self: 0.1, "ymin": lambda self: 0.1, "width": lambda self: 0.2, "height": lambda self: 0.5})()

        def get_label(self):
            return "person"

        def get_confidence(self):
            return 0.9

    class FakeRoi:
        def get_objects_typed(self, _kind):
            return [FakeDetection()]

    class FakeHailo:
        HAILO_DETECTION = object()

        @staticmethod
        def get_roi_from_buffer(_buffer):
            return FakeRoi()

    class FakePad:
        pass

    class FakeElement:
        @staticmethod
        def get_static_pad(_name):
            return FakePad()

    runner._on_handoff(
        element=FakeElement(),
        buffer=None,
        bindings={
            "hailo": FakeHailo(),
            "cv2": object(),
            "get_caps_from_pad": lambda _pad: ("RGB", 640, 640),
            "get_numpy_from_buffer": lambda *_args: (_ for _ in ()).throw(RuntimeError("snapshot failed")),
        },
    )

    assert published[0][0] == "bus"
    assert str(published[0][1]).startswith("Debug snapshot unavailable:")
    assert published[1][0] == "result"
    assert published[1][1].detections == [Detection("person", 0.9, (0.1, 0.1, 0.2, 0.5))]


def test_hailo_provider_reuses_one_session_for_same_camera(monkeypatch) -> None:
    created: list[tuple[object, ...]] = []

    class FakeSession:
        def __init__(self, camera: CameraConfig, fingerprint: tuple[object, ...]) -> None:
            self.camera = camera
            self.fingerprint = fingerprint
            created.append(fingerprint)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def has_exited(self) -> bool:
            return False

        def restart_ready(self) -> bool:
            return False

        def exit_error(self) -> str:
            return ""

        def latest_frame(self) -> HailoDetectionFrame:
            return HailoDetectionFrame([Detection("person", 0.9, (0.1, 0.1, 0.2, 0.5))])

    monkeypatch.setattr("backend.app.inference.hailo.HailoCameraSession", FakeSession)
    camera = CameraConfig("cam-1", "Entrance", "192.168.1.50", 554, "/live")
    first = MonitorConfig("mon-1", "Strict", "object_detector", "cam-1", config={"confidence_threshold": 0.9})
    second = MonitorConfig("mon-2", "Loose", "object_detector", "cam-1", config={"confidence_threshold": 0.5})
    provider = HailoGStreamerProvider()

    first_result = provider.result_for(first, camera)
    second_result = provider.result_for(second, camera)

    assert len(created) == 1
    assert [(item.monitor_id, item.value) for item in first_result.observations] == [
        ("mon-1", 1),
        ("mon-1", True),
    ]
    assert [(item.monitor_id, item.value) for item in second_result.observations] == [
        ("mon-2", 1),
        ("mon-2", True),
    ]
