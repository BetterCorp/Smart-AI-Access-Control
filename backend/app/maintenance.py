from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from backend.app.config import load_settings
from backend.app.db import Database, Repository
from backend.app.storage.snapshots import SnapshotStore, StorageConfig


DEFAULT_LOG_RETENTION_DAYS = 7
DEFAULT_LOG_MAX_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class CleanupReport:
    snapshots_deleted: int
    logs_deleted: int
    log_bytes_deleted: int


@dataclass(frozen=True)
class LogRecord:
    path: Path
    mtime: float
    bytes_len: int


def run_maintenance() -> CleanupReport:
    settings = load_settings()
    repository = Repository(Database(settings.db_path))
    snapshot_store = SnapshotStore(
        StorageConfig(
            snapshot_root=settings.snapshot_root,
            max_bytes=int(repository.get_setting("snapshot_max_bytes", str(10 * 1024 * 1024 * 1024))),
            min_free_disk_percent=float(repository.get_setting("snapshot_min_free_disk_percent", "20")),
            prune_batch_size=int(os.environ.get("SMARTAI_CLEANUP_SNAPSHOT_BATCH_SIZE", "500")),
        )
    )
    snapshots_deleted = snapshot_store.prune()
    logs_deleted, log_bytes_deleted = cleanup_logs(
        settings.data_dir,
        retention_days=int(os.environ.get("SMARTAI_LOG_RETENTION_DAYS", str(DEFAULT_LOG_RETENTION_DAYS))),
        max_total_bytes=int(os.environ.get("SMARTAI_LOG_MAX_BYTES", str(DEFAULT_LOG_MAX_BYTES))),
    )
    return CleanupReport(
        snapshots_deleted=len(snapshots_deleted),
        logs_deleted=logs_deleted,
        log_bytes_deleted=log_bytes_deleted,
    )


def cleanup_logs(
    data_dir: Path,
    *,
    retention_days: int = DEFAULT_LOG_RETENTION_DAYS,
    max_total_bytes: int = DEFAULT_LOG_MAX_BYTES,
    now: float | None = None,
) -> tuple[int, int]:
    records = log_records(data_dir)
    cutoff = (time.time() if now is None else now) - retention_days * 24 * 60 * 60
    deleted_count = 0
    deleted_bytes = 0

    kept: list[LogRecord] = []
    for record in records:
        if record.mtime < cutoff:
            if delete_path(record.path):
                deleted_count += 1
                deleted_bytes += record.bytes_len
            continue
        kept.append(record)

    total = sum(record.bytes_len for record in kept)
    ordered_kept = sorted(kept, key=lambda item: item.mtime)
    for record in ordered_kept[:-1]:
        if total <= max_total_bytes:
            break
        if delete_path(record.path):
            deleted_count += 1
            deleted_bytes += record.bytes_len
            total -= record.bytes_len

    return deleted_count, deleted_bytes


def log_records(data_dir: Path) -> list[LogRecord]:
    if not data_dir.exists():
        return []
    records: list[LogRecord] = []
    for path in data_dir.rglob("*"):
        if not path.is_file() or not is_log_file(path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        records.append(LogRecord(path, stat.st_mtime, stat.st_size))
    return records


def is_log_file(path: Path) -> bool:
    name = path.name
    return name.endswith(".log") or ".log." in name


def delete_path(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def main() -> None:
    report = run_maintenance()
    print(
        "cleanup complete: "
        f"snapshots_deleted={report.snapshots_deleted} "
        f"logs_deleted={report.logs_deleted} "
        f"log_bytes_deleted={report.log_bytes_deleted}"
    )


if __name__ == "__main__":
    main()
