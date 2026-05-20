from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
            return CameraSnapshotResult(None, detail or "snapshot pipeline completed but no JPEG was written.")
        return CameraSnapshotResult(output.read_bytes())


def capture_rtsp_jpeg(camera: CameraConfig, timeout_seconds: float = 8.0) -> bytes | None:
    return capture_rtsp_jpeg_result(camera, timeout_seconds).jpeg_bytes
