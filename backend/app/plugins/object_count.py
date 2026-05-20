from __future__ import annotations

from dataclasses import dataclass

from backend.app.domain import CameraConfig, Detection, Observation


@dataclass(frozen=True)
class ObjectCountConfig:
    class_name: str = "person"
    confidence_threshold: float = 0.5
    zone_id: str | None = None
    zone: tuple[float, float, float, float] | None = None


class ObjectCountPlugin:
    plugin_id = "core.object_count"
    name = "Object count"

    def on_detections(
        self,
        camera: CameraConfig,
        detections: list[Detection],
        config: ObjectCountConfig,
    ) -> list[Observation]:
        matches = [
            detection
            for detection in detections
            if detection.class_name == config.class_name
            if detection.confidence >= config.confidence_threshold
            if detection_in_zone(detection, config.zone)
        ]
        count = len(matches)
        metadata = {
            "className": config.class_name,
            "confidenceThreshold": config.confidence_threshold,
            "zone": zone_metadata(config.zone_id, config.zone),
            "detections": [
                {
                    "className": detection.class_name,
                    "confidence": detection.confidence,
                    "bbox": {
                        "x": detection.bbox[0],
                        "y": detection.bbox[1],
                        "width": detection.bbox[2],
                        "height": detection.bbox[3],
                    },
                }
                for detection in matches
            ],
        }
        return [
            Observation(
                plugin_id=self.plugin_id,
                camera_id=camera.id,
                metric=f"{config.class_name}.count",
                value=count,
                zone_id=config.zone_id,
                metadata=metadata,
            ),
            Observation(
                plugin_id=self.plugin_id,
                camera_id=camera.id,
                metric=f"{config.class_name}.present",
                value=count > 0,
                zone_id=config.zone_id,
                metadata=metadata,
            ),
        ]


def detection_in_zone(detection: Detection, zone: tuple[float, float, float, float] | None) -> bool:
    if zone is None:
        return True
    zone_x, zone_y, zone_width, zone_height = zone
    center_x = detection.bbox[0] + detection.bbox[2] / 2
    center_y = detection.bbox[1] + detection.bbox[3] / 2
    return zone_x <= center_x <= zone_x + zone_width and zone_y <= center_y <= zone_y + zone_height


def zone_metadata(zone_id: str | None, zone: tuple[float, float, float, float] | None) -> dict[str, object] | None:
    if zone is None:
        return None
    return {
        "id": zone_id,
        "x": zone[0],
        "y": zone[1],
        "width": zone[2],
        "height": zone[3],
    }
