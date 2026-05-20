from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


HAILO_MONITOR_POLL_SECONDS = 5.0
HAILO_TELEMETRY_IDLE_SECONDS = 30 * 60.0
HAILO_TELEMETRY_FILE = "hailo-telemetry.active"

_HAILO_CACHE: tuple[float, bool, dict[str, Any]] | None = None


def performance_snapshot(
    storage_path: Path,
    *,
    activate_hailo_telemetry: bool = False,
    hailo_telemetry_allowed: bool = True,
    hailo_telemetry_disabled_reason: str | None = None,
) -> dict[str, Any]:
    if activate_hailo_telemetry and hailo_telemetry_allowed:
        mark_hailo_telemetry_active(storage_path)
    return {
        "cpu": cpu_metrics(),
        "memory": memory_metrics(),
        "storage": storage_metrics(storage_path),
        "temperature": temperature_metrics(),
        "hailo": hailo_metrics(
            storage_path,
            telemetry_allowed=hailo_telemetry_allowed,
            disabled_reason=hailo_telemetry_disabled_reason,
        ),
    }


def cpu_metrics() -> dict[str, Any]:
    cores = os.cpu_count() or 1
    try:
        load1, load5, load15 = os.getloadavg()
    except (AttributeError, OSError):
        load1 = load5 = load15 = 0.0
    return {
        "cores": cores,
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
        "loadPercent": round(min((load1 / cores) * 100, 100), 1),
    }


def memory_metrics(meminfo_path: Path = Path("/proc/meminfo")) -> dict[str, Any]:
    info = read_meminfo(meminfo_path)
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    used = max(total - available, 0)
    return byte_usage(total * 1024, used * 1024)


def read_meminfo(path: Path) -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return info
    for line in lines:
        key, _, rest = line.partition(":")
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            info[key] = int(parts[0])
        except ValueError:
            continue
    return info


def storage_metrics(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path if path.exists() else path.parent)
    return byte_usage(usage.total, usage.used)


def temperature_metrics() -> dict[str, Any]:
    path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        celsius = int(path.read_text(encoding="utf-8").strip()) / 1000
    except (OSError, ValueError):
        return {"available": False, "celsius": None}
    return {"available": True, "celsius": round(celsius, 1)}


def mark_hailo_telemetry_active(storage_path: Path, idle_seconds: float = HAILO_TELEMETRY_IDLE_SECONDS) -> None:
    try:
        storage_path.mkdir(parents=True, exist_ok=True)
        telemetry_state_path(storage_path).write_text(str(time.time() + idle_seconds), encoding="utf-8")
    except OSError:
        pass


def hailo_telemetry_is_active(storage_path: Path) -> bool:
    try:
        active_until = float(telemetry_state_path(storage_path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return time.time() < active_until


def telemetry_state_path(storage_path: Path) -> Path:
    return storage_path / HAILO_TELEMETRY_FILE


def hailo_metrics(
    storage_path: Path,
    ttl_seconds: float = HAILO_MONITOR_POLL_SECONDS,
    *,
    telemetry_allowed: bool = True,
    disabled_reason: str | None = None,
) -> dict[str, Any]:
    global _HAILO_CACHE
    now = time.monotonic()
    telemetry_active = telemetry_allowed and hailo_telemetry_is_active(storage_path)

    if _HAILO_CACHE is not None and _HAILO_CACHE[1] == telemetry_active and now - _HAILO_CACHE[0] < ttl_seconds:
        metrics = dict(_HAILO_CACHE[2])
        metrics["telemetryAllowed"] = telemetry_allowed
        metrics["telemetryActive"] = telemetry_active
        if not telemetry_allowed:
            metrics.update(disabled_monitor_metrics(disabled_reason))
        return metrics

    device_nodes = sorted(glob.glob("/dev/hailo*"))
    pci = run_command(["lspci", "-nn"], timeout_seconds=2.0)
    identify = run_command(["hailortcli", "fw-control", "identify"], timeout_seconds=3.0)
    scan = run_command(["hailortcli", "scan"], timeout_seconds=3.0)
    monitor = (
        run_command(
            ["hailortcli", "monitor"],
            timeout_seconds=2.0,
            env={**os.environ, "HAILO_MONITOR": "1", "TERM": "dumb"},
        )
        if telemetry_active
        else CommandResult(0)
    )
    pci_output = pci.stdout + pci.stderr
    identify_output = identify.stdout + identify.stderr
    scan_output = scan.stdout + scan.stderr
    monitor_output = clean_terminal_text(monitor.stdout + monitor.stderr)
    monitor_stats = parse_hailo_monitor(monitor_output)
    metrics = {
        "pciDetected": "1e60:" in pci_output or "Hailo" in pci_output,
        "deviceNodes": device_nodes,
        "driverReady": bool(device_nodes),
        "identifyOk": identify.returncode == 0 and bool(identify.stdout.strip()),
        "architecture": parse_hailo_architecture(identify_output),
        "scanOk": scan.returncode == 0 and bool(scan_output.strip()),
        "scanSummary": first_non_empty_line(scan_output),
        "telemetryAllowed": telemetry_allowed,
        "telemetryActive": telemetry_active,
        "monitorOk": monitor_stats["hasData"],
        "monitorStatus": monitor_status(
            monitor,
            monitor_output,
            telemetry_active=telemetry_active,
            disabled_reason=disabled_reason if not telemetry_allowed else None,
        ),
        "monitorRaw": truncate_text(monitor_output, 2500),
        "monitorNoFiles": monitor_stats["noFiles"],
        "utilizationPercent": monitor_stats["utilizationPercent"],
        "fps": monitor_stats["fps"],
        "activeNetworkGroups": monitor_stats["activeNetworkGroups"],
    }
    if not telemetry_allowed:
        metrics.update(disabled_monitor_metrics(disabled_reason))
    _HAILO_CACHE = (now, telemetry_active, metrics)
    return metrics


def disabled_monitor_metrics(reason: str | None) -> dict[str, Any]:
    return {
        "monitorOk": False,
        "monitorStatus": reason or "telemetry disabled",
        "monitorRaw": "",
        "monitorNoFiles": False,
        "utilizationPercent": None,
        "fps": None,
        "activeNetworkGroups": 0,
    }


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def run_command(command: list[str], timeout_seconds: float, env: dict[str, str] | None = None) -> CommandResult:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, env=env)
    except FileNotFoundError:
        return CommandResult(127, stderr=f"{command[0]} not found")
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            124,
            stdout=timeout_text(exc.stdout),
            stderr=timeout_text(exc.stderr) or f"{command[0]} timed out",
        )
    return CommandResult(result.returncode, result.stdout, result.stderr)


def timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def parse_hailo_architecture(output: str) -> str | None:
    for line in output.splitlines():
        if "Device Architecture" in line:
            return line.split(":", 1)[1].strip()
    return None


def parse_hailo_monitor(output: str) -> dict[str, Any]:
    normalized = output.lower()
    no_files = "did not retrieve any files" in normalized or "no application currently running" in normalized
    if not output.strip() or no_files:
        return {
            "hasData": False,
            "utilizationPercent": None,
            "fps": None,
            "activeNetworkGroups": 0,
            "noFiles": no_files,
        }

    percent_values = [float(match) for match in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)\s*%", output)]
    fps_values = [
        float(match.group(1) or match.group(2))
        for match in re.finditer(
            r"(?i)(?:fps|frames/s)[^\n\d]*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:fps|frames/s)",
            output,
        )
    ]
    network_groups = {
        line.strip()
        for line in output.splitlines()
        if ".hef" in line.lower() or "vdevice" in line.lower()
    }
    return {
        "hasData": bool(percent_values or fps_values or network_groups),
        "utilizationPercent": round(max(percent_values), 1) if percent_values else None,
        "fps": round(max(fps_values), 2) if fps_values else None,
        "activeNetworkGroups": len(network_groups),
        "noFiles": False,
    }


def monitor_status(
    result: CommandResult,
    output: str,
    *,
    telemetry_active: bool = True,
    disabled_reason: str | None = None,
) -> str:
    if disabled_reason:
        return disabled_reason
    if not telemetry_active:
        return "telemetry idle"
    if result.returncode == 127:
        return "hailortcli not found"
    if "did not retrieve any files" in output.lower() or "no application currently running" in output.lower():
        return "no active Hailo telemetry files"
    if output.strip():
        return "sampled"
    if result.returncode == 124:
        return "timed out with no monitor data"
    if result.returncode == 0:
        return "no active monitor data"
    return "monitor command failed"


def clean_terminal_text(value: str) -> str:
    without_ansi = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", value)
    return "\n".join(line.rstrip() for line in without_ansi.splitlines())


def first_non_empty_line(value: str) -> str | None:
    for line in clean_terminal_text(value).splitlines():
        if line.strip():
            return line.strip()
    return None


def truncate_text(value: str, max_chars: int) -> str:
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def byte_usage(total: int, used: int) -> dict[str, Any]:
    percent = round((used / total) * 100, 1) if total else 0.0
    return {
        "totalBytes": total,
        "usedBytes": used,
        "freeBytes": max(total - used, 0),
        "usedPercent": percent,
        "totalHuman": human_bytes(total),
        "usedHuman": human_bytes(used),
        "freeHuman": human_bytes(max(total - used, 0)),
    }


def human_bytes(value: int) -> str:
    amount = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TB"
