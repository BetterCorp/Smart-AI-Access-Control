import os
from datetime import datetime, timezone
from pathlib import Path

from backend.app.maintenance import cleanup_logs, is_log_file


def test_cleanup_logs_deletes_old_logs(tmp_path) -> None:
    old_log = tmp_path / "hailort.log.1"
    new_log = tmp_path / "hailort.log"
    old_log.write_bytes(b"old")
    new_log.write_bytes(b"new")
    now = datetime(2026, 5, 24, tzinfo=timezone.utc).timestamp()
    old_time = now - 8 * 24 * 60 * 60
    os.utime(old_log, (old_time, old_time))
    os.utime(new_log, (now, now))

    deleted_count, deleted_bytes = cleanup_logs(tmp_path, retention_days=7, max_total_bytes=1024, now=now)

    assert deleted_count == 1
    assert deleted_bytes == 3
    assert not old_log.exists()
    assert new_log.exists()


def test_cleanup_logs_enforces_total_log_bytes(tmp_path) -> None:
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    first.write_bytes(b"123456")
    second.write_bytes(b"abcdef")
    now = datetime(2026, 5, 24, tzinfo=timezone.utc).timestamp()
    os.utime(first, (now - 10, now - 10))
    os.utime(second, (now, now))

    deleted_count, deleted_bytes = cleanup_logs(tmp_path, retention_days=7, max_total_bytes=8, now=now)

    assert deleted_count == 1
    assert deleted_bytes == 6
    assert not first.exists()
    assert second.exists()


def test_cleanup_logs_keeps_newest_log_even_when_over_limit(tmp_path) -> None:
    only = tmp_path / "hailort.log"
    only.write_bytes(b"123456")
    now = datetime(2026, 5, 24, tzinfo=timezone.utc).timestamp()
    os.utime(only, (now, now))

    deleted_count, deleted_bytes = cleanup_logs(tmp_path, retention_days=7, max_total_bytes=1, now=now)

    assert deleted_count == 0
    assert deleted_bytes == 0
    assert only.exists()


def test_is_log_file_matches_rotated_logs() -> None:
    assert is_log_file(Path("hailort.log"))
    assert is_log_file(Path("hailort.log.1"))
    assert not is_log_file(Path("image.jpg"))
