from __future__ import annotations

from dataclasses import dataclass

from backend.app.camera.rtsp import build_rtsp_url
from backend.app.domain import CameraConfig, InferenceResult, MonitorConfig


@dataclass(frozen=True)
class HailoPipelineConfig:
    hef_path: str
    labels_path: str
    batch_size: int = 1


class HailoRtspAdapter:
    """Boundary for the Raspberry Pi Hailo/GStreamer implementation.

    The production adapter should translate GStreamer/Hailo detection metadata
    into the common Observation list consumed by the rule engine.
    """

    def __init__(self, config: HailoPipelineConfig) -> None:
        self.config = config

    def pipeline_description(self, camera: CameraConfig) -> str:
        rtsp_url = build_rtsp_url(camera)
        return (
            f"rtspsrc location={rtsp_url} protocols={camera.transport} latency=100 ! "
            "rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! "
            f"hailonet hef-path={self.config.hef_path} batch-size={self.config.batch_size} ! "
            "hailofilter ! appsink name=detections sync=false max-buffers=1 drop=true"
        )

    def result_for(self, monitor: MonitorConfig, camera: CameraConfig) -> InferenceResult:
        raise NotImplementedError(
            "Wire this adapter on the Raspberry Pi after validating the exact Hailo/TAPPAS pipeline and metadata format."
        )
