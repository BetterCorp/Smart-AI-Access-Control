from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.camera.rtsp import build_rtsp_url
from backend.app.domain import CameraConfig


@dataclass(frozen=True)
class CameraSnapshotResult:
    jpeg_bytes: bytes | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.jpeg_bytes is not None


def capture_rtsp_jpeg_result(camera: CameraConfig, timeout_seconds: float = 8.0) -> CameraSnapshotResult:
    appsink_result = capture_rtsp_jpeg_with_appsink(camera, timeout_seconds)
    if appsink_result.ok:
        return appsink_result
    with tempfile.TemporaryDirectory(prefix="smartai-frame-") as temp_dir:
        output = Path(temp_dir) / "snapshot.jpg"
        command = [
            "gst-launch-1.0",
            "-q",
            "-e",
            "rtspsrc",
            f"location={build_rtsp_url(camera)}",
            "latency=100",
            "protocols=tcp",
            "!",
            "application/x-rtp,media=video",
            "!",
            "decodebin",
            "!",
            "videoconvert",
            "!",
            "videoscale",
            "!",
            "video/x-raw,format=RGB,width=640,height=360",
            "!",
            "identity",
            "eos-after=1",
            "!",
            "jpegenc",
            "quality=85",
            "!",
            "filesink",
            f"location={output}",
        ]
        try:
            completed = subprocess.run(command, check=True, capture_output=True, timeout=timeout_seconds, text=True)
        except FileNotFoundError:
            return CameraSnapshotResult(None, "gst-launch-1.0 was not found.")
        except subprocess.TimeoutExpired:
            return CameraSnapshotResult(None, f"snapshot capture timed out after {timeout_seconds:.0f}s.")
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            return CameraSnapshotResult(None, detail or f"snapshot pipeline failed with exit code {exc.returncode}.")
        if not output.exists() or output.stat().st_size == 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            fallback_error = detail or "gst-launch fallback completed but no JPEG was written."
            return CameraSnapshotResult(None, f"{appsink_result.error}; {fallback_error}")
        return CameraSnapshotResult(output.read_bytes())


def capture_rtsp_jpeg(camera: CameraConfig, timeout_seconds: float = 8.0) -> bytes | None:
    return capture_rtsp_jpeg_result(camera, timeout_seconds).jpeg_bytes


def capture_rtsp_jpeg_with_appsink(camera: CameraConfig, timeout_seconds: float) -> CameraSnapshotResult:
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        import cv2
        import numpy as np
    except Exception as exc:
        return CameraSnapshotResult(None, f"appsink capture unavailable: {exc}")

    Gst.init(None)
    pipeline = None
    try:
        pipeline = Gst.parse_launch(appsink_pipeline(build_rtsp_url(camera)))
        sink = pipeline.get_by_name("snapshot_sink")
        if sink is None:
            return CameraSnapshotResult(None, "appsink capture pipeline is missing snapshot_sink.")
        pipeline.set_state(Gst.State.PLAYING)
        sample = sink.emit("try-pull-sample", int(timeout_seconds * Gst.SECOND))
        if sample is None:
            return CameraSnapshotResult(None, f"appsink capture timed out after {timeout_seconds:.0f}s without a video frame.")
        return jpeg_from_sample(sample, Gst, cv2, np)
    except Exception as exc:
        return CameraSnapshotResult(None, f"appsink capture failed: {exc}")
    finally:
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)


def appsink_pipeline(rtsp_url: str) -> str:
    return (
        f'rtspsrc location="{rtsp_url}" latency=100 protocols=tcp ! '
        "application/x-rtp,media=video ! "
        "decodebin ! "
        "videoconvert ! "
        "videoscale ! "
        "video/x-raw,format=RGB,width=640,height=360 ! "
        "appsink name=snapshot_sink emit-signals=false sync=false max-buffers=1 drop=true"
    )


def jpeg_from_sample(sample: Any, Gst: Any, cv2: Any, np: Any) -> CameraSnapshotResult:
    caps = sample.get_caps()
    structure = caps.get_structure(0) if caps is not None and caps.get_size() else None
    if structure is None:
        return CameraSnapshotResult(None, "appsink sample did not include video caps.")
    width = int(structure.get_value("width"))
    height = int(structure.get_value("height"))
    buffer = sample.get_buffer()
    ok, info = buffer.map(Gst.MapFlags.READ)
    if not ok:
        return CameraSnapshotResult(None, "appsink sample buffer could not be mapped.")
    try:
        expected = width * height * 3
        frame = np.frombuffer(info.data, dtype=np.uint8)
        if frame.size < expected:
            return CameraSnapshotResult(None, f"appsink frame too small: got {frame.size} bytes, expected {expected}.")
        rgb = frame[:expected].reshape((height, width, 3))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        ok, encoded = cv2.imencode(".jpg", bgr)
        if not ok:
            return CameraSnapshotResult(None, "appsink frame could not be JPEG encoded.")
        return CameraSnapshotResult(encoded.tobytes())
    finally:
        buffer.unmap(info)
