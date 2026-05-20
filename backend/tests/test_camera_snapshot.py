from types import SimpleNamespace

from backend.app.camera.snapshot import capture_rtsp_jpeg
from backend.app.domain import CameraConfig


def test_capture_rtsp_jpeg_returns_file_bytes(monkeypatch) -> None:
    def fake_run(command, check, capture_output, timeout):
        output = next(item.split("=", 1)[1] for item in command if item.startswith("location=") and item.endswith(".jpg"))
        with open(output, "wb") as handle:
            handle.write(b"jpeg")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("backend.app.camera.snapshot.subprocess.run", fake_run)

    assert capture_rtsp_jpeg(CameraConfig("cam-1", "Entrance", "host", 554, "/live")) == b"jpeg"
