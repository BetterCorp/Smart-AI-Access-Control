import pytest

from backend.app.domain import RelayDesiredState
from backend.app.relay.service import RelayArbiter, RelayCommand, RelayConflictError


def test_highest_priority_relay_command_wins() -> None:
    arbiter = RelayArbiter()

    result = arbiter.resolve(
        [
            RelayCommand("relay-1", RelayDesiredState.OFF, "rule-low", 10),
            RelayCommand("relay-1", RelayDesiredState.ON, "rule-high", 100),
        ]
    )

    assert result["relay-1"] == RelayDesiredState.ON


def test_equal_priority_conflict_is_rejected() -> None:
    arbiter = RelayArbiter()

    with pytest.raises(RelayConflictError):
        arbiter.resolve(
            [
                RelayCommand("relay-1", RelayDesiredState.OFF, "rule-a", 100),
                RelayCommand("relay-1", RelayDesiredState.ON, "rule-b", 100),
            ]
        )

