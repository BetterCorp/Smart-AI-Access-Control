from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backend.app.domain import SnapshotRef, utc_now


@dataclass(frozen=True)
class StorageConfig:
    snapshot_root: Path
    max_bytes: int = 10 * 1024 * 1024 * 1024
    min_free_disk_percent: float = 20.0
    prune_batch_size: int = 100


@dataclass(frozen=True)
class SnapshotRecord:
    event_id: str
    path: Path
    created_at: datetime
    bytes_len: int


class SnapshotStore:
    def __init__(self, config: StorageConfig) -> None:
        self.config = config
        self.config.snapshot_root.mkdir(parents=True, exist_ok=True)

    def write_snapshot(self, event_id: str, jpeg_bytes: bytes, created_at: datetime | None = None) -> SnapshotRef:
        digest = hashlib.sha256(jpeg_bytes).hexdigest()
        filename = f"{event_id}.jpg"
        path = self.config.snapshot_root / filename
        path.write_bytes(jpeg_bytes)
        if created_at is not None:
            timestamp = created_at.timestamp()
            import os

            os.utime(path, (timestamp, timestamp))
        return SnapshotRef(
            event_id=event_id,
            path=str(path),
            mime_type="image/jpeg",
            filename=filename,
            sha256=digest,
            bytes_len=len(jpeg_bytes),
        )

    def write_monitor_debug_snapshot(self, monitor_id: str, jpeg_bytes: bytes) -> Path:
        debug_root = self.config.snapshot_root / "monitor-debug"
        debug_root.mkdir(parents=True, exist_ok=True)
        path = debug_root / f"{monitor_id}.jpg"
        path.write_bytes(jpeg_bytes)
        return path

    def records(self) -> list[SnapshotRecord]:
        records = []
        for path in self.config.snapshot_root.glob("*.jpg"):
            stat = path.stat()
            records.append(
                SnapshotRecord(
                    event_id=path.stem,
                    path=path,
                    created_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
                    bytes_len=stat.st_size,
                )
            )
        return sorted(records, key=lambda item: item.created_at)

    def current_usage_bytes(self) -> int:
        return sum(record.bytes_len for record in self.records())

    def should_store(self, dedupe_key: str, previous_key: str | None, min_interval_elapsed: bool) -> bool:
        return dedupe_key != previous_key and min_interval_elapsed

    def prune(self) -> list[Path]:
        deleted: list[Path] = []
        records = self.records()
        usage = sum(record.bytes_len for record in records)

        for record in records:
            if usage <= self.config.max_bytes and self._free_disk_percent() >= self.config.min_free_disk_percent:
                break
            record.path.unlink(missing_ok=True)
            deleted.append(record.path)
            usage -= record.bytes_len
            if len(deleted) >= self.config.prune_batch_size:
                break
        return deleted

    def _free_disk_percent(self) -> float:
        usage = shutil.disk_usage(self.config.snapshot_root)
        return (usage.free / usage.total) * 100


def snapshot_dedupe_key(
    rule_id: str,
    camera_id: str,
    state: str,
    metric: str,
    value: object,
    zone_id: str | None,
) -> str:
    return "|".join([rule_id, camera_id, state, metric, str(value), zone_id or ""])


def event_id(prefix: str = "evt") -> str:
    return f"{prefix}_{utc_now().strftime('%Y%m%d%H%M%S%f')}"
