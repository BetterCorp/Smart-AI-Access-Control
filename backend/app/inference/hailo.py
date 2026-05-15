from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from backend.app.camera.rtsp import build_rtsp_url
from backend.app.domain import CameraConfig, Detection, InferenceResult, MonitorConfig, Observation
from backend.app.plugins.object_count import ObjectCountConfig, ObjectCountPlugin


@dataclass(frozen=True)
class HailoPipelineResources:
    hef_path: str
    post_process_so: str
    post_function_name: str
    labels_json: str | None


class HailoGStreamerProvider:
    """Runs one live Hailo/GStreamer session per active monitor."""

    def __init__(self) -> None:
        self._sessions: dict[str, HailoMonitorSession] = {}
        self._lock = threading.Lock()

    def result_for(self, monitor: MonitorConfig, camera: CameraConfig) -> InferenceResult:
        if monitor.model_id != "person_counter":
            raise RuntimeError(
                f"{monitor.model_id} needs a dedicated Hailo detector before it can run in real inference mode."
            )

        fingerprint = (
            build_rtsp_url(camera),
            camera.transport,
            camera.analytics_fps,
            monitor.model_id,
            tuple(sorted(monitor.config.items())),
        )
        with self._lock:
            session = self._sessions.get(monitor.id)
            if session is None or session.fingerprint != fingerprint:
                if session is not None:
                    session.stop()
                session = HailoMonitorSession(monitor, camera, fingerprint)
                self._sessions[monitor.id] = session
                session.start()

        return session.latest_result()

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.stop()


class HailoMonitorSession:
    def __init__(
        self,
        monitor: MonitorConfig,
        camera: CameraConfig,
        fingerprint: tuple[object, ...],
    ) -> None:
        self.monitor = monitor
        self.camera = camera
        self.fingerprint = fingerprint
        self._latest_result: InferenceResult | None = None
        self._latest_error: str | None = None
        self._loop: Any = None
        self._pipeline: Any = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"hailo-monitor-{self.monitor.id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        pipeline = self._pipeline
        loop = self._loop
        if pipeline is not None:
            try:
                from gi.repository import Gst

                pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
        if loop is not None:
            try:
                loop.quit()
            except Exception:
                pass

    def latest_result(self) -> InferenceResult:
        with self._lock:
            if self._latest_result is not None:
                return self._latest_result
            if self._latest_error is not None:
                raise RuntimeError(self._latest_error)
        raise RuntimeError("Waiting for first Hailo frame from the RTSP stream.")

    def _run(self) -> None:
        try:
            bindings = load_hailo_bindings()
            resources = resolve_detection_resources(bindings)
            pipeline_string = build_detection_pipeline(
                bindings,
                build_rtsp_url(self.camera),
                resources,
                max(1, round(self.camera.analytics_fps)),
            )
            Gst = bindings["Gst"]
            GLib = bindings["GLib"]
            Gst.init(None)
            pipeline = Gst.parse_launch(pipeline_string)
            identity = pipeline.get_by_name("identity_callback")
            if identity is None:
                raise RuntimeError("Hailo pipeline is missing identity_callback.")
            identity.set_property("signal-handoffs", True)
            identity.connect("handoff", self._on_handoff, bindings)

            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_bus_message, bindings)

            loop = GLib.MainLoop()
            self._pipeline = pipeline
            self._loop = loop
            pipeline.set_state(Gst.State.PLAYING)
            loop.run()
        except Exception as exc:
            self._set_error(str(exc))
        finally:
            pipeline = self._pipeline
            if pipeline is not None:
                try:
                    pipeline.set_state(load_hailo_bindings()["Gst"].State.NULL)
                except Exception:
                    pass

    def _on_handoff(self, element: Any, buffer: Any, bindings: dict[str, Any]) -> None:
        try:
            hailo = bindings["hailo"]
            cv2 = bindings["cv2"]
            get_caps_from_pad = bindings["get_caps_from_pad"]
            get_numpy_from_buffer = bindings["get_numpy_from_buffer"]

            roi = hailo.get_roi_from_buffer(buffer)
            hailo_detections = roi.get_objects_typed(hailo.HAILO_DETECTION)
            detections = [detection_from_hailo(item) for item in hailo_detections]
            observations = observations_for(self.monitor, self.camera, detections)

            debug_jpeg = None
            pad = element.get_static_pad("sink")
            if pad is not None:
                frame_format, width, height = get_caps_from_pad(pad)
                if frame_format is not None and width is not None and height is not None:
                    frame = get_numpy_from_buffer(buffer, frame_format, width, height)
                    if frame is not None:
                        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        ok, encoded = cv2.imencode(".jpg", bgr)
                        if ok:
                            debug_jpeg = encoded.tobytes()

            with self._lock:
                self._latest_result = InferenceResult(observations, debug_jpeg=debug_jpeg)
                self._latest_error = None
        except Exception as exc:
            self._set_error(str(exc))

    def _on_bus_message(self, _bus: Any, message: Any, bindings: dict[str, Any]) -> None:
        Gst = bindings["Gst"]
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            suffix = f" ({debug})" if debug else ""
            self._set_error(f"GStreamer error: {error}{suffix}")
        elif message.type == Gst.MessageType.EOS:
            self._set_error("GStreamer stream ended.")

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._latest_error = message


def observations_for(
    monitor: MonitorConfig,
    camera: CameraConfig,
    detections: list[Detection],
) -> list[Observation]:
    config = ObjectCountConfig(
        class_name=str(monitor.config.get("class_name", "person")),
        confidence_threshold=float(monitor.config.get("confidence_threshold", 0.5)),
    )
    observations = ObjectCountPlugin().on_detections(camera, detections, config)
    return [
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
        )
        for observation in observations
    ]


def detection_from_hailo(raw_detection: Any) -> Detection:
    bbox = raw_detection.get_bbox()
    return Detection(
        class_name=str(raw_detection.get_label()),
        confidence=float(raw_detection.get_confidence()),
        bbox=(
            float(bbox.xmin()),
            float(bbox.ymin()),
            float(bbox.width()),
            float(bbox.height()),
        ),
    )


def load_hailo_bindings() -> dict[str, Any]:
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import GLib, Gst
        import cv2
        import hailo
        from hailo_apps.python.core.common.buffer_utils import get_caps_from_pad, get_numpy_from_buffer
        from hailo_apps.python.core.common.core import get_resource_path, resolve_hef_path
        from hailo_apps.python.core.common.defines import (
            DETECTION_PIPELINE,
            DETECTION_POSTPROCESS_FUNCTION,
            DETECTION_POSTPROCESS_SO_FILENAME,
            RESOURCES_SO_DIR_NAME,
        )
        from hailo_apps.python.core.common.hef_utils import get_hef_labels_json
        from hailo_apps.python.core.common.installation_utils import detect_hailo_arch
        from hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines import (
            INFERENCE_PIPELINE,
            INFERENCE_PIPELINE_WRAPPER,
            SOURCE_PIPELINE,
            TRACKER_PIPELINE,
            USER_CALLBACK_PIPELINE,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"Hailo Python/GStreamer bindings are unavailable: missing Python module '{exc.name}'. "
            "Re-run setup with Hailo support enabled."
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Hailo Python/GStreamer bindings are unavailable: {exc}. "
            "Re-run setup with Hailo support enabled."
        ) from exc

    return {
        "GLib": GLib,
        "Gst": Gst,
        "cv2": cv2,
        "hailo": hailo,
        "get_caps_from_pad": get_caps_from_pad,
        "get_numpy_from_buffer": get_numpy_from_buffer,
        "get_resource_path": get_resource_path,
        "resolve_hef_path": resolve_hef_path,
        "DETECTION_PIPELINE": DETECTION_PIPELINE,
        "DETECTION_POSTPROCESS_FUNCTION": DETECTION_POSTPROCESS_FUNCTION,
        "DETECTION_POSTPROCESS_SO_FILENAME": DETECTION_POSTPROCESS_SO_FILENAME,
        "RESOURCES_SO_DIR_NAME": RESOURCES_SO_DIR_NAME,
        "get_hef_labels_json": get_hef_labels_json,
        "detect_hailo_arch": detect_hailo_arch,
        "SOURCE_PIPELINE": SOURCE_PIPELINE,
        "INFERENCE_PIPELINE": INFERENCE_PIPELINE,
        "INFERENCE_PIPELINE_WRAPPER": INFERENCE_PIPELINE_WRAPPER,
        "TRACKER_PIPELINE": TRACKER_PIPELINE,
        "USER_CALLBACK_PIPELINE": USER_CALLBACK_PIPELINE,
    }


def resolve_detection_resources(bindings: dict[str, Any]) -> HailoPipelineResources:
    arch = bindings["detect_hailo_arch"]()
    hef_path = bindings["resolve_hef_path"](
        None,
        app_name=bindings["DETECTION_PIPELINE"],
        arch=arch,
    )
    post_process_so = bindings["get_resource_path"](
        bindings["DETECTION_PIPELINE"],
        bindings["RESOURCES_SO_DIR_NAME"],
        arch,
        bindings["DETECTION_POSTPROCESS_SO_FILENAME"],
    )
    labels_json = bindings["get_hef_labels_json"](hef_path)
    return HailoPipelineResources(
        hef_path=str(hef_path),
        post_process_so=str(post_process_so),
        post_function_name=str(bindings["DETECTION_POSTPROCESS_FUNCTION"]),
        labels_json=str(labels_json) if labels_json else None,
    )


def build_detection_pipeline(
    bindings: dict[str, Any],
    rtsp_url: str,
    resources: HailoPipelineResources,
    analytics_fps: int,
) -> str:
    source = bindings["SOURCE_PIPELINE"](
        rtsp_url,
        video_width=640,
        video_height=640,
        frame_rate=analytics_fps,
        sync=True,
    )
    inference = bindings["INFERENCE_PIPELINE"](
        hef_path=resources.hef_path,
        post_process_so=resources.post_process_so,
        post_function_name=resources.post_function_name,
        batch_size=1,
        config_json=resources.labels_json,
        additional_params=(
            "nms-score-threshold=0.3 "
            "nms-iou-threshold=0.45 "
            "output-format-type=HAILO_FORMAT_TYPE_FLOAT32"
        ),
    )
    inference_wrapper = bindings["INFERENCE_PIPELINE_WRAPPER"](inference)
    tracker = bindings["TRACKER_PIPELINE"](class_id=1)
    callback = bindings["USER_CALLBACK_PIPELINE"]()
    return (
        f"{source} ! "
        f"{inference_wrapper} ! "
        f"{tracker} ! "
        "hailooverlay ! "
        "videoconvert ! "
        "video/x-raw,format=RGB ! "
        f"{callback} ! "
        "fakesink sync=false"
    )
