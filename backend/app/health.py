from __future__ import annotations

from typing import Any


def camera_row_is_fault(row: Any) -> bool:
    last_error = row["last_error"] or ""
    if last_error.startswith("Waiting for first Hailo frame"):
        return False
    return row["health"] in {"stream_error", "auth_failed"}


def camera_display_health(row: Any) -> str:
    last_error = row["last_error"] or ""
    if last_error.startswith("Waiting for first Hailo frame"):
        return "waiting"
    return str(row["health"])
