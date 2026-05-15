from backend.app.camera.rtsp import build_rtsp_url, mask_rtsp_url
from backend.app.domain import CameraConfig


def test_build_rtsp_url_encodes_credentials_and_path() -> None:
    camera = CameraConfig(
        id="cam-1",
        name="Entrance",
        host="192.168.1.50",
        port=554,
        username="user name",
        password="p@ss/word",
        path="Streaming/Channels/102",
    )

    assert build_rtsp_url(camera) == "rtsp://user%20name:p%40ss%2Fword@192.168.1.50:554/Streaming/Channels/102"


def test_mask_rtsp_url_does_not_leak_password() -> None:
    camera = CameraConfig(
        id="cam-1",
        name="Entrance",
        host="camera.local",
        port=8554,
        username="admin",
        password="secret",
        path="/live",
    )

    masked = mask_rtsp_url(camera)

    assert "secret" not in masked
    assert masked == "rtsp://admin:%2A%2A%2A@camera.local:8554/live"

