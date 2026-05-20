from __future__ import annotations

from dataclasses import dataclass

from backend.app.domain import AIModelDefinition, MetricDefinition, MetricValueType


COCO_DETECTION_CLASSES = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
]


OBJECT_DETECTOR = AIModelDefinition(
    id="object_detector",
    name="Object Detector",
    description="Counts visible objects from the built-in YOLOv8s detector.",
    actual_model="YOLOv8s object detector",
    analytics_uses=["object counting", "object presence", "zone-aware detection"],
    metrics=[
        MetricDefinition("<class>.count", "Object Count", MetricValueType.NUMBER, "Number of visible objects for the selected class."),
        MetricDefinition("<class>.present", "Object Present", MetricValueType.BOOLEAN, "True when at least one selected object is visible."),
    ],
)


@dataclass(frozen=True)
class MonitorTemplate:
    id: str
    name: str
    class_name: str
    metric_count: str
    metric_present: str


MONITOR_TEMPLATES = [
    MonitorTemplate(
        id=class_name.replace(" ", "_"),
        name=f"{class_name.title()} Counter",
        class_name=class_name,
        metric_count=f"{class_name}.count",
        metric_present=f"{class_name}.present",
    )
    for class_name in COCO_DETECTION_CLASSES
]


MODEL_DEFINITIONS = {
    OBJECT_DETECTOR.id: OBJECT_DETECTOR,
}


def model_options() -> list[AIModelDefinition]:
    return list(MODEL_DEFINITIONS.values())


def monitor_templates() -> list[MonitorTemplate]:
    return MONITOR_TEMPLATES
