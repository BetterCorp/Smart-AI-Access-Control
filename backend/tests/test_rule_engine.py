from datetime import datetime, timedelta, timezone

from backend.app.domain import (
    ConditionGroup,
    Observation,
    RelayAction,
    RelayDesiredState,
    RuleCondition,
    RuleConfig,
    RuleState,
)
from backend.app.rules.engine import RuleEngine, condition_matches


def test_condition_matches_numeric_operator() -> None:
    observation = Observation("core.object_count", "cam-1", "person.count", 2)
    condition = RuleCondition("person.count", ">=", 2)

    assert condition_matches(condition, observation)


def test_rule_debounces_before_emitting_actions() -> None:
    engine = RuleEngine()
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    action = RelayAction("relay-1", RelayDesiredState.ON)
    rule = RuleConfig(
        id="rule-1",
        name="Mantrap",
        enabled=True,
        priority=100,
        monitor_id="mon-1",
        condition_group=ConditionGroup("all", [RuleCondition("person.count", ">=", 2)]),
        true_actions=[action],
        debounce_true_ms=1000,
    )
    observations = [Observation("core.object_count", "cam-1", "person.count", 2, monitor_id="mon-1")]

    first = engine.evaluate(rule, observations, now)
    second = engine.evaluate(rule, observations, now + timedelta(milliseconds=999))
    third = engine.evaluate(rule, observations, now + timedelta(milliseconds=1000))

    assert first.state == RuleState.TRUE_PENDING
    assert first.actions == []
    assert second.state == RuleState.TRUE_PENDING
    assert second.actions == []
    assert third.state == RuleState.TRUE
    assert third.actions == [action]


def test_rule_fault_emits_fault_actions() -> None:
    engine = RuleEngine()
    action = RelayAction("relay-1", RelayDesiredState.OFF)
    rule = RuleConfig(
        id="rule-1",
        name="Mantrap",
        enabled=True,
        priority=100,
        monitor_id="mon-1",
        condition_group=ConditionGroup("all", [RuleCondition("person.count", ">=", 2)]),
        fault_actions=[action],
    )

    result = engine.evaluate(rule, [], datetime(2026, 5, 15, tzinfo=timezone.utc), faulted=True)

    assert result.state == RuleState.FAULT
    assert result.actions == [action]


def test_rule_supports_boolean_presence_conditions() -> None:
    engine = RuleEngine()
    action = RelayAction("relay-1", RelayDesiredState.ON)
    rule = RuleConfig(
        id="rule-present",
        name="Person present",
        enabled=True,
        priority=100,
        monitor_id="mon-present",
        condition_group=ConditionGroup("all", [RuleCondition("person.present", "is_true", True)]),
        true_actions=[action],
    )
    observations = [Observation("core.object_count", "cam-1", "person.present", True, monitor_id="mon-present")]

    result = engine.evaluate(rule, observations, datetime(2026, 5, 15, tzinfo=timezone.utc))

    assert result.state == RuleState.TRUE
    assert result.actions == [action]
