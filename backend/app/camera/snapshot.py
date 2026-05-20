from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from backend.app.camera.rtsp import build_rtsp_url
from backend.app.domain import CameraConfig


def capture_rtsp_jpeg(camera: CameraConfig, timeout_seconds: float = 8.0) -> bytes | None:
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
            subprocess.run(command, check=True, capture_output=True, timeout=timeout_seconds)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        if not output.exists() or output.stat().st_size == 0:
            return None
        return output.read_bytes()
