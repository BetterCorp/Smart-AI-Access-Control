from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RuleState(str, Enum):
    UNKNOWN = "unknown"
    TRUE_PENDING = "true_pending"
    TRUE = "true"
    FALSE_PENDING = "false_pending"
    FALSE = "false"
    FAULT = "fault"


class RelayDesiredState(str, Enum):
    OFF = "off"
    ON = "on"


class FailPolicy(str, Enum):
    GLOBAL = "global"
    FAIL_OFF = "fail_off"
    FAIL_ON = "fail_on"
    HOLD_STATE = "hold_state"


class SnapshotDelivery(str, Enum):
    URL_ONLY = "url_only"
    MULTIPART = "multipart"
    BASE64_JSON = "base64_json"


class MetricValueType(str, Enum):
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING = "string"


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    name: str
    value_type: MetricValueType
    description: str = ""


@dataclass(frozen=True)
class AIModelDefinition:
    id: str
    name: str
    description: str
    actual_model: str
    analytics_uses: list[str]
    metrics: list[MetricDefinition]


@dataclass(frozen=True)
class MonitorConfig:
    id: str
    name: str
    model_id: str
    camera_id: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CameraConfig:
    id: str
    name: str
    host: str
    port: int
    path: str
    username: str | None = None
    password: str | None = None
    transport: str = "tcp"
    analytics_fps: float = 2.0


@dataclass(frozen=True)
class Detection:
    class_name: str
    confidence: float
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class Observation:
    plugin_id: str
    camera_id: str
    metric: str
    value: int | float | bool | str
    timestamp: datetime = field(default_factory=utc_now)
    monitor_id: str | None = None
    model_id: str | None = None
    zone_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InferenceResult:
    observations: list[Observation]
    debug_jpeg: bytes | None = None


@dataclass(frozen=True)
class RuleCondition:
    metric: str
    operator: str
    value: int | float | bool | str


@dataclass(frozen=True)
class ConditionGroup:
    mode: str
    conditions: list[RuleCondition]


@dataclass(frozen=True)
class RelayAction:
    relay_channel_id: str
    desired_state: RelayDesiredState


@dataclass(frozen=True)
class WebhookAction:
    url: str
    snapshot_delivery: SnapshotDelivery = SnapshotDelivery.MULTIPART
    timeout_ms: int = 5000
    retry_count: int = 2
    headers: dict[str, str] = field(default_factory=dict)
    hmac_secret: str | None = None


Action = RelayAction | WebhookAction


@dataclass(frozen=True)
class RuleConfig:
    id: str
    name: str
    enabled: bool
    priority: int
    monitor_id: str
    condition_group: ConditionGroup
    true_actions: list[Action] = field(default_factory=list)
    false_actions: list[Action] = field(default_factory=list)
    fault_actions: list[Action] = field(default_factory=list)
    debounce_true_ms: int = 0
    debounce_false_ms: int = 0
    cooldown_ms: int = 0
    fail_policy: FailPolicy = FailPolicy.GLOBAL

    @property
    def camera_ids(self) -> list[str]:
        return []

    @property
    def plugin_id(self) -> str:
        return ""

    @property
    def condition(self) -> RuleCondition:
        return self.condition_group.conditions[0]


@dataclass
class RuleRuntimeState:
    state: RuleState = RuleState.UNKNOWN
    pending_since: datetime | None = None
    last_transition_at: datetime | None = None
    last_actions_at: datetime | None = None
    last_observation_value: Any = None


@dataclass(frozen=True)
class SnapshotRef:
    event_id: str
    path: str
    mime_type: str
    filename: str
    sha256: str
    bytes_len: int
