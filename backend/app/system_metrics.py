from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_HAILO_CACHE: tuple[float, dict[str, Any]] | None = None


def performance_snapshot(storage_path: Path) -> dict[str, Any]:
    return {
        "cpu": cpu_metrics(),
        "memory": memory_metrics(),
        "storage": storage_metrics(storage_path),
        "temperature": temperature_metrics(),
        "hailo": hailo_metrics(),
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


def hailo_metrics(ttl_seconds: float = 30.0) -> dict[str, Any]:
    global _HAILO_CACHE
    now = time.monotonic()
    if _HAILO_CACHE is not None and now - _HAILO_CACHE[0] < ttl_seconds:
        return _HAILO_CACHE[1]

    device_nodes = sorted(glob.glob("/dev/hailo*"))
    pci = run_command(["lspci", "-nn"], timeout_seconds=2.0)
    identify = run_command(["hailortcli", "fw-control", "identify"], timeout_seconds=3.0)
    pci_output = pci.stdout + pci.stderr
    identify_output = identify.stdout + identify.stderr
    metrics = {
        "pciDetected": "1e60:" in pci_output or "Hailo" in pci_output,
        "deviceNodes": device_nodes,
        "driverReady": bool(device_nodes),
        "identifyOk": identify.returncode == 0 and bool(identify.stdout.strip()),
        "architecture": parse_hailo_architecture(identify_output),
    }
    _HAILO_CACHE = (now, metrics)
    return metrics


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def run_command(command: list[str], timeout_seconds: float) -> CommandResult:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
    except FileNotFoundError:
        return CommandResult(127, stderr=f"{command[0]} not found")
    except subprocess.TimeoutExpired:
        return CommandResult(124, stderr=f"{command[0]} timed out")
    return CommandResult(result.returncode, result.stdout, result.stderr)


def parse_hailo_architecture(output: str) -> str | None:
    for line in output.splitlines():
        if "Device Architecture" in line:
            return line.split(":", 1)[1].strip()
    return None


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
