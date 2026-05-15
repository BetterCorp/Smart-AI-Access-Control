from __future__ import annotations

from backend.app.domain import AIModelDefinition, MetricDefinition, MetricValueType


PERSON_COUNTER = AIModelDefinition(
    id="person_counter",
    name="Person Counter",
    description="Counts people and exposes count/presence metrics.",
    actual_model="YOLOv8 object detector",
    analytics_uses=[
        "occupancy counting",
        "mantrap occupancy",
        "people presence",
    ],
    metrics=[
        MetricDefinition("person.count", "Person Count", MetricValueType.NUMBER, "Number of visible people."),
        MetricDefinition("person.present", "Person Present", MetricValueType.BOOLEAN, "True when at least one person is visible."),
    ],
)


WEAPON_VISIBILITY = AIModelDefinition(
    id="weapon_visibility",
    name="Weapon Visibility",
    description="Detects whether a configured weapon class is visible.",
    actual_model="Custom YOLO-family object detector",
    analytics_uses=[
        "weapon presence alerting",
        "prohibited-object detection",
    ],
    metrics=[
        MetricDefinition("weapon.visible", "Weapon Visible", MetricValueType.BOOLEAN, "True when a weapon is visible."),
        MetricDefinition("weapon.count", "Weapon Count", MetricValueType.NUMBER, "Number of visible weapons."),
    ],
)


MODEL_DEFINITIONS = {
    PERSON_COUNTER.id: PERSON_COUNTER,
    WEAPON_VISIBILITY.id: WEAPON_VISIBILITY,
}


def model_options() -> list[AIModelDefinition]:
    return list(MODEL_DEFINITIONS.values())
