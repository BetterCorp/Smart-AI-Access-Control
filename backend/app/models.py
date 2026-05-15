from __future__ import annotations

from backend.app.domain import AIModelDefinition, MetricDefinition, MetricValueType


PERSON_COUNTER = AIModelDefinition(
    id="person_counter",
    name="Person Counter",
    description="Counts people and exposes count/presence metrics.",
    metrics=[
        MetricDefinition("person.count", "Person Count", MetricValueType.NUMBER, "Number of visible people."),
        MetricDefinition("person.present", "Person Present", MetricValueType.BOOLEAN, "True when at least one person is visible."),
    ],
)


WEAPON_VISIBILITY = AIModelDefinition(
    id="weapon_visibility",
    name="Weapon Visibility",
    description="Detects whether a configured weapon class is visible.",
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
