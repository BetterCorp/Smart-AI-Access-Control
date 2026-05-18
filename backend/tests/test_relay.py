from types import SimpleNamespace

import pytest
import subprocess

from backend.app.domain import RelayDesiredState
from backend.app.relay.service import RelayArbiter, RelayCommand, RelayConflictError, UsbRelayDriver


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


def test_usbrelay_maps_default_board_to_single_detected_board(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, check, capture_output=False, text=False, timeout=None):
        calls.append(args)
        if len(args) == 1:
            return SimpleNamespace(stdout="REL0A_1=0\nREL0A_2=0\nREL0A_3=0\nREL0A_4=0\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("backend.app.relay.service.subprocess.run", fake_run)

    driver = UsbRelayDriver()

    assert driver.device_count() == 4
    driver.set_channel("board-1", 2, RelayDesiredState.ON)
    assert calls[-1] == ["usbrelay", "REL0A_2=1"]


def test_usbrelay_timeout_returns_no_channels(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("usbrelay", timeout=2)

    monkeypatch.setattr("backend.app.relay.service.subprocess.run", fake_run)

    assert UsbRelayDriver().list_channels() == []
