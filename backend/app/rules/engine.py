from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from backend.app.domain import (
    Action,
    Observation,
    RuleCondition,
    RuleConfig,
    RuleRuntimeState,
    RuleState,
)


@dataclass(frozen=True)
class RuleEvaluation:
    rule_id: str
    previous_state: RuleState
    state: RuleState
    actions: list[Action]
    matched_observation: Observation | None


def condition_matches(condition: RuleCondition, observation: Observation) -> bool:
    if observation.metric != condition.metric:
        return False

    left = observation.value
    right = condition.value

    if condition.operator == "==":
        return left == right
    if condition.operator == "!=":
        return left != right
    if condition.operator in {">", ">=", "<", "<="}:
        if isinstance(left, bool) or isinstance(right, bool):
            raise ValueError("ordered comparisons do not support boolean values")
        if condition.operator == ">":
            return left > right
        if condition.operator == ">=":
            return left >= right
        if condition.operator == "<":
            return left < right
        return left <= right

    raise ValueError(f"unsupported rule operator: {condition.operator}")


class RuleEngine:
    def __init__(self) -> None:
        self._states: dict[str, RuleRuntimeState] = {}

    def state_for(self, rule_id: str) -> RuleRuntimeState:
        return self._states.setdefault(rule_id, RuleRuntimeState())

    def evaluate(
        self,
        rule: RuleConfig,
        observations: Iterable[Observation],
        now: datetime,
        faulted: bool = False,
    ) -> RuleEvaluation:
        runtime = self.state_for(rule.id)
        previous_state = runtime.state

        if not rule.enabled:
            runtime.state = RuleState.FALSE
            return RuleEvaluation(rule.id, previous_state, runtime.state, [], None)

        if faulted:
            runtime.state = RuleState.FAULT
            runtime.pending_since = None
            actions = self._transition_actions(rule, previous_state, runtime.state, now, rule.fault_actions)
            return RuleEvaluation(rule.id, previous_state, runtime.state, actions, None)

        matched = None
        is_true = False
        for observation in observations:
            if observation.camera_id not in rule.camera_ids:
                continue
            if condition_matches(rule.condition, observation):
                matched = observation
                is_true = True
                break
            if observation.metric == rule.condition.metric:
                matched = observation

        target = RuleState.TRUE if is_true else RuleState.FALSE
        debounce_ms = rule.debounce_true_ms if is_true else rule.debounce_false_ms
        next_state = self._debounced_state(runtime, target, debounce_ms, now)
        runtime.state = next_state
        runtime.last_observation_value = matched.value if matched else None

        stable_target_reached = runtime.state == target
        action_set = rule.true_actions if target == RuleState.TRUE else rule.false_actions
        actions = []
        if stable_target_reached:
            actions = self._transition_actions(rule, previous_state, runtime.state, now, action_set)

        return RuleEvaluation(rule.id, previous_state, runtime.state, actions, matched)

    def _debounced_state(
        self,
        runtime: RuleRuntimeState,
        target: RuleState,
        debounce_ms: int,
        now: datetime,
    ) -> RuleState:
        pending_state = RuleState.TRUE_PENDING if target == RuleState.TRUE else RuleState.FALSE_PENDING

        if runtime.state == target:
            runtime.pending_since = None
            return target

        if debounce_ms <= 0:
            runtime.pending_since = None
            return target

        if runtime.state != pending_state:
            runtime.pending_since = now
            return pending_state

        if runtime.pending_since is None:
            runtime.pending_since = now
            return pending_state

        elapsed_ms = (now - runtime.pending_since).total_seconds() * 1000
        if elapsed_ms >= debounce_ms:
            runtime.pending_since = None
            return target

        return pending_state

    def _transition_actions(
        self,
        rule: RuleConfig,
        previous: RuleState,
        current: RuleState,
        now: datetime,
        actions: list[Action],
    ) -> list[Action]:
        runtime = self.state_for(rule.id)
        if previous == current:
            return []
        if rule.cooldown_ms > 0 and runtime.last_actions_at is not None:
            elapsed_ms = (now - runtime.last_actions_at).total_seconds() * 1000
            if elapsed_ms < rule.cooldown_ms:
                return []
        runtime.last_transition_at = now
        runtime.last_actions_at = now
        return list(actions)

