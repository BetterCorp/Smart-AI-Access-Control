from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FrameTicket:
    camera_id: str
    requested_at: datetime


class InferenceScheduler:
    def __init__(self, global_max_fps: float) -> None:
        if global_max_fps <= 0:
            raise ValueError("global_max_fps must be positive")
        self.global_max_fps = global_max_fps
        self._camera_order: deque[str] = deque()

    def configure_cameras(self, camera_ids: list[str]) -> None:
        self._camera_order = deque(camera_ids)

    def next_ticket(self, now: datetime) -> FrameTicket | None:
        if not self._camera_order:
            return None
        camera_id = self._camera_order.popleft()
        self._camera_order.append(camera_id)
        return FrameTicket(camera_id=camera_id, requested_at=now)

