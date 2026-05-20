from __future__ import annotations

import multiprocessing as mp
import os
import queue
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
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


@dataclass(frozen=True)
class HailoDetectionFrame:
    detections: list[Detection]
    debug_jpeg: bytes | None = None


class HailoGStreamerProvider:
    """Runs one live Hailo/GStreamer detector session per camera/model."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[object, ...], HailoCameraSession] = {}
        self._lock = threading.Lock()
        self._telemetry_enabled = False

    def result_for(self, monitor: MonitorConfig, camera: CameraConfig) -> InferenceResult:
        if monitor.model_id not in {"object_detector", "person_counter"}:
            raise RuntimeError(
                f"{monitor.model_id} needs a dedicated Hailo detector before it can run in real inference mode."
            )

        fingerprint = self.session_fingerprint(camera, "yolov8s")
        with self._lock:
            session = self._sessions.get(fingerprint)
            if session is None:
                session = HailoCameraSession(camera, fingerprint, telemetry_enabled=self._telemetry_enabled)
                self._sessions[fingerprint] = session
                session.start()
            elif session.has_exited():
                if not session.restart_ready():
                    raise RuntimeError(session.exit_error())
                session.stop()
                session = HailoCameraSession(camera, fingerprint, telemetry_enabled=self._telemetry_enabled)
                self._sessions[fingerprint] = session
                session.start()

        frame = session.latest_frame()
        return InferenceResult(
            observations_for(monitor, camera, frame.detections),
            debug_jpeg=frame.debug_jpeg,
        )

    def prune_sessions(self, monitors: list[MonitorConfig], cameras: dict[str, CameraConfig]) -> None:
        active = {
            self.session_fingerprint(camera, "yolov8s")
            for monitor in monitors
            if monitor.enabled
            if monitor.model_id in {"object_detector", "person_counter"}
            if (camera := cameras.get(monitor.camera_id)) is not None
        }
        with self._lock:
            unused_keys = [key for key in self._sessions if key not in active]
            sessions = [self._sessions.pop(key) for key in unused_keys]
        for session in sessions:
            session.stop()

    @staticmethod
    def session_fingerprint(camera: CameraConfig, detector_model_id: str) -> tuple[object, ...]:
        return (
            camera.id,
            build_rtsp_url(camera),
            camera.transport,
            camera.analytics_fps,
            detector_model_id,
        )

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.stop()

    def set_telemetry_enabled(self, enabled: bool) -> None:
        with self._lock:
            if enabled == self._telemetry_enabled:
                return
            self._telemetry_enabled = enabled
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.stop()


class HailoCameraSession:
    restart_backoff_seconds = 5.0

    def __init__(
        self,
        camera: CameraConfig,
        fingerprint: tuple[object, ...],
        *,
        telemetry_enabled: bool = False,
    ) -> None:
        self.camera = camera
        self.fingerprint = fingerprint
        self.telemetry_enabled = telemetry_enabled
        self._latest_frame: HailoDetectionFrame | None = None
        self._latest_error: str | None = None
        self._latest_bus_message: str | None = None
        self._started_at = time.monotonic()
        self._restart_after: float | None = None
        self._queue: Any = mp.get_context("spawn").Queue(maxsize=3)
        self._process: mp.Process | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._process = mp.get_context("spawn").Process(
            target=run_hailo_child,
            args=(self.camera, self._queue, self.telemetry_enabled),
            name=f"hailo-camera-{self.camera.id}",
            daemon=True,
        )
        self._process.start()

    def stop(self) -> None:
        process = self._process
        if process is None:
            return
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)

    def has_exited(self) -> bool:
        process = self._process
        if process is None or process.exitcode is None:
            return False
        with self._lock:
            if self._restart_after is None:
                self._restart_after = time.monotonic() + self.restart_backoff_seconds
        return True

    def restart_ready(self) -> bool:
        with self._lock:
            return self._restart_after is not None and time.monotonic() >= self._restart_after

    def exit_error(self) -> str:
        process = self._process
        if process is None or process.exitcode is None:
            return "Hailo pipeline process is not running."
        return format_child_exit(process.exitcode)

    def latest_frame(self) -> HailoDetectionFrame:
        self._drain_messages()
        with self._lock:
            if self._latest_frame is not None:
                return self._latest_frame
            if self._latest_error is not None:
                raise RuntimeError(self._latest_error)
            process = self._process
            if process is not None and process.exitcode is not None:
                raise RuntimeError(format_child_exit(process.exitcode))
            waited = time.monotonic() - self._started_at
            bus_message = self._latest_bus_message
        detail = f" Latest pipeline message: {bus_message}" if bus_message else ""
        if waited >= 10:
            raise RuntimeError(
                f"No Hailo frame received after {waited:.0f}s from the RTSP pipeline.{detail}"
            )
        raise RuntimeError(f"Waiting for first Hailo frame from the RTSP pipeline ({waited:.0f}s).{detail}")

    def _drain_messages(self) -> None:
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                if kind == "result":
                    self._latest_frame = payload
                    self._latest_error = None
                elif kind == "error":
                    self._latest_error = str(payload)
                elif kind == "bus":
                    self._latest_bus_message = str(payload)


class HailoPipelineRunner:
    def __init__(self, camera: CameraConfig, output_queue: Any) -> None:
        self.camera = camera
        self.output_queue = output_queue
        self._loop: Any = None
        self._pipeline: Any = None

    def run(self) -> None:
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
            self._publish("error", str(exc))
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

            roi = hailo.get_roi_from_buffer(buffer)
            hailo_detections = roi.get_objects_typed(hailo.HAILO_DETECTION)
            detections = [detection_from_hailo(item) for item in hailo_detections]
            debug_jpeg = self._build_debug_jpeg(element, buffer, bindings)
            self._publish("result", HailoDetectionFrame(detections, debug_jpeg=debug_jpeg))
        except Exception as exc:
            self._publish("error", str(exc))

    def _build_debug_jpeg(self, element: Any, buffer: Any, bindings: dict[str, Any]) -> bytes | None:
        try:
            cv2 = bindings["cv2"]
            get_caps_from_pad = bindings["get_caps_from_pad"]
            get_numpy_from_buffer = bindings["get_numpy_from_buffer"]
            pad = element.get_static_pad("sink")
            if pad is None:
                return None
            frame_format, width, height = get_caps_from_pad(pad)
            if frame_format is None or width is None or height is None:
                return None
            try:
                frame = get_numpy_from_buffer(buffer, frame_format, width, height)
            except Exception:
                frame = self._map_debug_frame(buffer, frame_format, width, height, bindings)
            if frame is None:
                return None
            if self._frame_looks_blank(frame, bindings):
                self._publish("bus", "Debug snapshot unavailable: frame was blank.")
                return None
            bgr = frame if frame_format == "BGR" else cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, encoded = cv2.imencode(".jpg", bgr)
            return encoded.tobytes() if ok else None
        except Exception as exc:
            self._publish("bus", f"Debug snapshot unavailable: {exc}")
            return None

    def _map_debug_frame(self, buffer: Any, frame_format: str, width: int, height: int, bindings: dict[str, Any]) -> Any:
        if frame_format not in {"RGB", "BGR"}:
            return None
        Gst = bindings["Gst"]
        np = bindings["np"]
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            expected = width * height * 3
            frame = np.frombuffer(info.data, dtype=np.uint8)
            if frame.size < expected:
                return None
            return frame[:expected].reshape((height, width, 3)).copy()
        finally:
            buffer.unmap(info)

    def _frame_looks_blank(self, frame: Any, bindings: dict[str, Any]) -> bool:
        np = bindings["np"]
        return float(np.std(frame)) < 1.0 and (float(np.mean(frame)) < 2.0 or float(np.mean(frame)) > 253.0)

    def _on_bus_message(self, _bus: Any, message: Any, bindings: dict[str, Any]) -> None:
        Gst = bindings["Gst"]
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            suffix = f" ({debug})" if debug else ""
            self._publish("error", f"GStreamer error: {error}{suffix}")
        elif message.type == Gst.MessageType.EOS:
            self._publish("error", "GStreamer stream ended.")
        elif message.type == Gst.MessageType.WARNING:
            warning, debug = message.parse_warning()
            suffix = f" ({debug})" if debug else ""
            self._publish("bus", f"GStreamer warning: {warning}{suffix}")
        elif message.type == Gst.MessageType.STATE_CHANGED and message.src == self._pipeline:
            old_state, new_state, _pending = message.parse_state_changed()
            self._publish(
                "bus",
                f"Pipeline state changed from {old_state.value_nick} to {new_state.value_nick}."
            )

    def _publish(self, kind: str, payload: object) -> None:
        try:
            self.output_queue.put_nowait((kind, payload))
        except queue.Full:
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                pass
            self.output_queue.put_nowait((kind, payload))


def run_hailo_child(camera: CameraConfig, output_queue: Any, telemetry_enabled: bool = False) -> None:
    os.environ["HAILO_MONITOR"] = "1" if telemetry_enabled else "0"
    os.environ.setdefault("HAILO_MONITOR_TIME_INTERVAL", "5000")
    HailoPipelineRunner(camera, output_queue).run()


def format_child_exit(exitcode: int) -> str:
    if exitcode < 0:
        try:
            signal_name = signal.Signals(-exitcode).name
        except ValueError:
            signal_name = f"signal {-exitcode}"
        return f"Hailo pipeline process exited with {signal_name}."
    return f"Hailo pipeline process exited with code {exitcode}."


def observations_for(
    monitor: MonitorConfig,
    camera: CameraConfig,
    detections: list[Detection],
) -> list[Observation]:
    config = ObjectCountConfig(
        class_name=str(monitor.config.get("class_name", "person")),
        confidence_threshold=float(monitor.config.get("confidence_threshold", 0.5)),
        zone_id=str(monitor.config.get("zone_id")) if monitor.config.get("zone_id") else None,
        zone=monitor_zone(monitor),
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
            metadata=observation.metadata,
        )
        for observation in observations
    ]


def monitor_zone(monitor: MonitorConfig) -> tuple[float, float, float, float] | None:
    raw_zone = monitor.config.get("zone")
    if not isinstance(raw_zone, dict):
        return None
    try:
        x = float(raw_zone["x"])
        y = float(raw_zone["y"])
        width = float(raw_zone["width"])
        height = float(raw_zone["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    width = min(width, 1.0 - x)
    height = min(height, 1.0 - y)
    return (x, y, width, height)


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
        import numpy as np
        from hailo_apps.python.core.common.buffer_utils import get_caps_from_pad, get_numpy_from_buffer
        from hailo_apps.python.core.common.core import get_resource_path, resolve_hef_path
        from hailo_apps.python.core.common.defines import (
            DEFAULT_DOTENV_PATH,
            DETECTION_PIPELINE,
            DETECTION_POSTPROCESS_FUNCTION,
            DETECTION_POSTPROCESS_SO_FILENAME,
            RESOURCES_SO_DIR_NAME,
        )
        from hailo_apps.python.core.common.core import load_environment
        from hailo_apps.python.core.common.hef_utils import get_hef_labels_json
        from hailo_apps.python.core.common.installation_utils import detect_hailo_arch
        from hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines import (
            INFERENCE_PIPELINE,
            INFERENCE_PIPELINE_WRAPPER,
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
        "np": np,
        "hailo": hailo,
        "get_caps_from_pad": get_caps_from_pad,
        "get_numpy_from_buffer": get_numpy_from_buffer,
        "get_resource_path": get_resource_path,
        "resolve_hef_path": resolve_hef_path,
        "load_environment": load_environment,
        "DEFAULT_DOTENV_PATH": DEFAULT_DOTENV_PATH,
        "DETECTION_PIPELINE": DETECTION_PIPELINE,
        "DETECTION_POSTPROCESS_FUNCTION": DETECTION_POSTPROCESS_FUNCTION,
        "DETECTION_POSTPROCESS_SO_FILENAME": DETECTION_POSTPROCESS_SO_FILENAME,
        "RESOURCES_SO_DIR_NAME": RESOURCES_SO_DIR_NAME,
        "get_hef_labels_json": get_hef_labels_json,
        "detect_hailo_arch": detect_hailo_arch,
        "INFERENCE_PIPELINE": INFERENCE_PIPELINE,
        "INFERENCE_PIPELINE_WRAPPER": INFERENCE_PIPELINE_WRAPPER,
        "TRACKER_PIPELINE": TRACKER_PIPELINE,
        "USER_CALLBACK_PIPELINE": USER_CALLBACK_PIPELINE,
    }


def resolve_detection_resources(bindings: dict[str, Any]) -> HailoPipelineResources:
    bindings["load_environment"](bindings["DEFAULT_DOTENV_PATH"])
    arch = bindings["detect_hailo_arch"]()
    hef_path = bindings["resolve_hef_path"](
        None,
        app_name=bindings["DETECTION_PIPELINE"],
        arch=arch,
        app_type="pipeline",
    )
    if hef_path is None:
        raise RuntimeError(
            f"No default Hailo detection model is available for architecture '{arch}'. "
            "Re-run setup so Hailo detection resources are installed."
        )
    post_process_so = bindings["get_resource_path"](
        bindings["DETECTION_PIPELINE"],
        bindings["RESOURCES_SO_DIR_NAME"],
        arch,
        bindings["DETECTION_POSTPROCESS_SO_FILENAME"],
    )
    if post_process_so is None:
        raise RuntimeError(
            f"No Hailo detection post-process library is available for architecture '{arch}'. "
            "Re-run setup so Hailo post-install completes."
        )
    labels_json = bindings["get_hef_labels_json"](hef_path)
    ensure_readable_resource(hef_path, "Hailo detection model")
    ensure_readable_resource(post_process_so, "Hailo detection post-process library")
    if labels_json:
        ensure_readable_resource(labels_json, "Hailo label metadata")
    return HailoPipelineResources(
        hef_path=str(hef_path),
        post_process_so=str(post_process_so),
        post_function_name=str(bindings["DETECTION_POSTPROCESS_FUNCTION"]),
        labels_json=str(labels_json) if labels_json else None,
    )


def ensure_readable_resource(path: str | Path, label: str) -> None:
    resource = Path(path)
    if not resource.is_file():
        raise RuntimeError(f"{label} is missing at '{resource}'. Re-run setup so Hailo resources are installed.")
    if not os.access(resource, os.R_OK):
        raise RuntimeError(
            f"{label} is not readable by the worker at '{resource}'. "
            "Re-run setup so Hailo resource permissions are repaired."
        )


def build_detection_pipeline(
    bindings: dict[str, Any],
    rtsp_url: str,
    resources: HailoPipelineResources,
    analytics_fps: int,
) -> str:
    source = build_rtsp_video_source_pipeline(rtsp_url, analytics_fps)
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
        "videoconvert ! "
        "video/x-raw,format=RGB ! "
        f"{callback} ! "
        "fakesink sync=false"
    )


def build_rtsp_video_source_pipeline(rtsp_url: str, analytics_fps: int) -> str:
    return (
        f'rtspsrc location="{rtsp_url}" latency=100 name=source ! '
        "application/x-rtp,media=video ! "
        "queue name=source_queue_decode leaky=no max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! "
        "decodebin name=source_decodebin ! "
        "queue name=source_scale_q leaky=no max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! "
        "videoscale name=source_videoscale n-threads=2 ! "
        "queue name=source_convert_q leaky=no max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! "
        "videoconvert n-threads=3 name=source_convert qos=false ! "
        "video/x-raw,pixel-aspect-ratio=1/1,format=RGB,width=640,height=640 ! "
        f'videorate name=source_videorate ! capsfilter name=source_fps_caps caps="video/x-raw,framerate={analytics_fps}/1"'
    )
