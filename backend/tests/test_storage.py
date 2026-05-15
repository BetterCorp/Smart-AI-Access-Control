from datetime import datetime, timezone

from backend.app.storage.snapshots import SnapshotStore, StorageConfig, snapshot_dedupe_key


def test_snapshot_dedupe_key_changes_when_value_changes() -> None:
    first = snapshot_dedupe_key("rule-1", "cam-1", "true", "person.count", 1, None)
    second = snapshot_dedupe_key("rule-1", "cam-1", "true", "person.count", 2, None)

    assert first != second


def test_snapshot_prune_deletes_oldest_until_under_limit(tmp_path) -> None:
    store = SnapshotStore(StorageConfig(snapshot_root=tmp_path, max_bytes=8, min_free_disk_percent=0))

    store.write_snapshot("evt-old", b"123456", datetime(2026, 5, 15, 8, 0, tzinfo=timezone.utc))
    store.write_snapshot("evt-new", b"abcdef", datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc))

    deleted = store.prune()

    assert tmp_path / "evt-old.jpg" in deleted
    assert not (tmp_path / "evt-old.jpg").exists()
    assert (tmp_path / "evt-new.jpg").exists()
    assert store.current_usage_bytes() <= 8

