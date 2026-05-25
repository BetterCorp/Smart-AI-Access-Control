from backend.app.system_metrics import (
    CommandResult,
    hailo_metrics,
    human_bytes,
    mark_hailo_telemetry_active,
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
    assert metrics["noFiles"] is False


def test_parse_hailo_monitor_detects_no_files_message() -> None:
    metrics = parse_hailo_monitor(
        "Monitor did not retrieve any files. This occurs when there is no application currently running.\n"
        "Device ID Utilization (%) Architecture\n"
    )

    assert metrics["hasData"] is False
    assert metrics["noFiles"] is True


def test_hailo_monitor_probe_is_skipped_when_telemetry_is_idle(monkeypatch, tmp_path) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], timeout_seconds: float, env=None, cwd=None) -> CommandResult:
        commands.append(command)
        return CommandResult(0, stdout="Device Architecture: HAILO8L\n")

    monkeypatch.setattr("backend.app.system_metrics._HAILO_CACHE", None)
    monkeypatch.setattr("backend.app.system_metrics.glob.glob", lambda pattern: ["/dev/hailo0"])
    monkeypatch.setattr("backend.app.system_metrics.run_command", fake_run)

    metrics = hailo_metrics(tmp_path, ttl_seconds=0)

    assert metrics["telemetryActive"] is False
    assert ["hailortcli", "monitor"] not in commands


def test_hailo_monitor_probe_runs_when_telemetry_is_active(monkeypatch, tmp_path) -> None:
    commands: list[list[str]] = []
    monitor_env: dict[str, str] = {}
    monitor_cwd = None

    def fake_run(command: list[str], timeout_seconds: float, env=None, cwd=None) -> CommandResult:
        nonlocal monitor_env, monitor_cwd
        commands.append(command)
        if command == ["hailortcli", "monitor"]:
            monitor_env = dict(env or {})
            monitor_cwd = cwd
            return CommandResult(124, stdout="Device Utilization 25%\nyolov8s.hef FPS 8\n")
        return CommandResult(0, stdout="Device Architecture: HAILO8L\n")

    monkeypatch.setattr("backend.app.system_metrics._HAILO_CACHE", None)
    monkeypatch.setattr("backend.app.system_metrics.glob.glob", lambda pattern: ["/dev/hailo0"])
    monkeypatch.setattr("backend.app.system_metrics.run_command", fake_run)
    mark_hailo_telemetry_active(tmp_path)

    metrics = hailo_metrics(tmp_path, ttl_seconds=0)

    assert metrics["telemetryActive"] is True
    assert ["hailortcli", "monitor"] in commands
    assert monitor_env["HAILORT_LOGGER_PATH"] == str(tmp_path)
    assert monitor_cwd == tmp_path
    assert metrics["utilizationPercent"] == 25.0
    assert metrics["fps"] == 8.0


def test_hailo_monitor_probe_is_disabled_when_telemetry_not_allowed(monkeypatch, tmp_path) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], timeout_seconds: float, env=None, cwd=None) -> CommandResult:
        commands.append(command)
        return CommandResult(0, stdout="Device Architecture: HAILO8L\n")

    monkeypatch.setattr("backend.app.system_metrics._HAILO_CACHE", None)
    monkeypatch.setattr("backend.app.system_metrics.glob.glob", lambda pattern: ["/dev/hailo0"])
    monkeypatch.setattr("backend.app.system_metrics.run_command", fake_run)
    mark_hailo_telemetry_active(tmp_path)

    metrics = hailo_metrics(
        tmp_path,
        ttl_seconds=0,
        telemetry_allowed=False,
        disabled_reason="real inference not active",
    )

    assert metrics["telemetryAllowed"] is False
    assert metrics["telemetryActive"] is False
    assert metrics["monitorStatus"] == "real inference not active"
    assert ["hailortcli", "monitor"] not in commands
