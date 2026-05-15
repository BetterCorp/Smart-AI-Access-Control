from __future__ import annotations

from urllib.parse import quote

from backend.app.domain import CameraConfig


def build_rtsp_url(camera: CameraConfig, include_password: bool = True) -> str:
    path = camera.path if camera.path.startswith("/") else f"/{camera.path}"
    auth = ""
    if camera.username:
        username = quote(camera.username, safe="")
        password = ""
        if camera.password is not None:
            password_value = camera.password if include_password else "***"
            password = f":{quote(password_value, safe='')}"
        auth = f"{username}{password}@"
    return f"rtsp://{auth}{camera.host}:{camera.port}{path}"


def mask_rtsp_url(camera: CameraConfig) -> str:
    return build_rtsp_url(camera, include_password=False)

