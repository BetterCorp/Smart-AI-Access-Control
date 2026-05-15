from __future__ import annotations

import subprocess
from dataclasses import dataclass

from backend.app.domain import FailPolicy, RelayAction, RelayDesiredState, RuleConfig


@dataclass(frozen=True)
class RelayChannelConfig:
    id: str
    board_id: str
    channel_number: int
    name: str
    default_state: RelayDesiredState = RelayDesiredState.OFF
    global_fail_state: FailPolicy = FailPolicy.FAIL_OFF


@dataclass(frozen=True)
class RelayCommand:
    channel_id: str
    state: RelayDesiredState
    source_rule_id: str
    priority: int


class RelayConflictError(ValueError):
    pass


class RelayArbiter:
    def resolve(self, commands: list[RelayCommand]) -> dict[str, RelayDesiredState]:
        winners: dict[str, RelayCommand] = {}
        for command in commands:
            current = winners.get(command.channel_id)
            if current is None or command.priority > current.priority:
                winners[command.channel_id] = command
                continue
            if current.priority == command.priority and current.state != command.state:
                raise RelayConflictError(
                    f"conflicting relay commands for {command.channel_id} at priority {command.priority}"
                )
        return {channel_id: command.state for channel_id, command in winners.items()}


def relay_commands_for_rule(rule: RuleConfig, actions: list[object]) -> list[RelayCommand]:
    commands: list[RelayCommand] = []
    for action in actions:
        if isinstance(action, RelayAction):
            commands.append(
                RelayCommand(
                    channel_id=action.relay_channel_id,
                    state=action.desired_state,
                    source_rule_id=rule.id,
                    priority=rule.priority,
                )
            )
    return commands


class UsbRelayDriver:
    def __init__(self, executable: str = "usbrelay") -> None:
        self.executable = executable

    def list_channels(self) -> list[str]:
        try:
            result = subprocess.run([self.executable], check=True, capture_output=True, text=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return []
        return [line.split("=", 1)[0].strip() for line in result.stdout.splitlines() if line.strip()]

    def device_count(self) -> int:
        return len(self.list_channels())

    def set_channel(self, board_id: str, channel_number: int, state: RelayDesiredState) -> None:
        value = "1" if state == RelayDesiredState.ON else "0"
        target = f"{self._resolve_board_id(board_id)}_{channel_number}={value}"
        subprocess.run([self.executable, target], check=True)

    def _resolve_board_id(self, board_id: str) -> str:
        if board_id != "board-1":
            return board_id
        detected_board_ids = {channel.rsplit("_", 1)[0] for channel in self.list_channels() if "_" in channel}
        if len(detected_board_ids) == 1:
            return next(iter(detected_board_ids))
        return board_id
