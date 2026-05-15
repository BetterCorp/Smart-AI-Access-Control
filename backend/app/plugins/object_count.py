from __future__ import annotations

from dataclasses import dataclass

from backend.app.domain import CameraConfig, Detection, Observation


@dataclass(frozen=True)
class ObjectCountConfig:
    class_name: str = "person"
    confidence_threshold: float = 0.5
    zone_id: str | None = None


class ObjectCountPlugin:
    plugin_id = "core.object_count"
    name = "Object count"

    def on_detections(
        self,
        camera: CameraConfig,
        detections: list[Detection],
        config: ObjectCountConfig,
    ) -> list[Observation]:
        count = sum(
            1
            for detection in detections
            if detection.class_name == config.class_name and detection.confidence >= config.confidence_threshold
        )
        return [
            Observation(
                plugin_id=self.plugin_id,
                camera_id=camera.id,
                metric=f"{config.class_name}.count",
                value=count,
                zone_id=config.zone_id,
            ),
            Observation(
                plugin_id=self.plugin_id,
                camera_id=camera.id,
                metric=f"{config.class_name}.present",
                value=count > 0,
                zone_id=config.zone_id,
            ),
        ]

