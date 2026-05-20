from backend.app.system_metrics import human_bytes, memory_metrics, parse_hailo_architecture, parse_hailo_monitor


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
