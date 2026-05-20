from backend.app.system_metrics import (
    CommandResult,
    hailo_metrics,
    human_bytes,
    memory_metrics,
    parse_hailo_architecture,
    parse_hailo_monitor,
)


def test_human_bytes_formats_values() -> None:
    assert human_bytes(1024 * 1024) == "1.0 MB"


def test_memory_metrics_reads_proc_meminfo(tmp_path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        1000 kB\nMemAvailable:     250 kB\n", encoding="utf-8")

    metrics = memory_metrics(meminfo)

    assert metrics["usedBytes"] == 750 * 1024
    assert metrics["usedPercent"] == 75.0


def test_parse_hailo_architecture() -> None:
    assert parse_hailo_architecture("Device Architecture: HAILO8L\n") == "HAILO8L"


def test_parse_hailo_monitor_extracts_usage_and_fps() -> None:
    output = """
    Devices
    Device Utilization 42.5%
    Network Groups
    yolov8s.hef FPS 18.75
    """

    metrics = parse_hailo_monitor(output)

    assert metrics["hasData"] is True
    assert metrics["utilizationPercent"] == 42.5
    assert metrics["fps"] == 18.75
    assert metrics["activeNetworkGroups"] == 1


def test_hailo_monitor_probe_is_skipped_when_telemetry_is_idle(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], timeout_seconds: float, env=None) -> CommandResult:
        commands.append(command)
        return CommandResult(0, stdout="Device Architecture: HAILO8L\n")

    monkeypatch.setattr("backend.app.system_metrics._HAILO_CACHE", None)
    monkeypatch.setattr("backend.app.system_metrics._HAILO_TELEMETRY_ACTIVE_UNTIL", 0.0)
    monkeypatch.setattr("backend.app.system_metrics.glob.glob", lambda pattern: ["/dev/hailo0"])
    monkeypatch.setattr("backend.app.system_metrics.run_command", fake_run)

    metrics = hailo_metrics(ttl_seconds=0, activate=False)

    assert metrics["telemetryActive"] is False
    assert ["hailortcli", "monitor"] not in commands


def test_hailo_monitor_probe_runs_when_telemetry_is_active(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], timeout_seconds: float, env=None) -> CommandResult:
        commands.append(command)
        if command == ["hailortcli", "monitor"]:
            return CommandResult(124, stdout="Device Utilization 25%\nyolov8s.hef FPS 8\n")
        return CommandResult(0, stdout="Device Architecture: HAILO8L\n")

    monkeypatch.setattr("backend.app.system_metrics._HAILO_CACHE", None)
    monkeypatch.setattr("backend.app.system_metrics._HAILO_TELEMETRY_ACTIVE_UNTIL", 0.0)
    monkeypatch.setattr("backend.app.system_metrics.glob.glob", lambda pattern: ["/dev/hailo0"])
    monkeypatch.setattr("backend.app.system_metrics.run_command", fake_run)

    metrics = hailo_metrics(ttl_seconds=0, activate=True)

    assert metrics["telemetryActive"] is True
    assert ["hailortcli", "monitor"] in commands
    assert metrics["utilizationPercent"] == 25.0
    assert metrics["fps"] == 8.0
