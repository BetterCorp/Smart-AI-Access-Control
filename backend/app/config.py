from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    snapshot_root: Path
    use_mock_inference: bool
    enable_relay_hardware: bool


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("SMARTAI_DATA_DIR", ".smartai"))
    return Settings(
        data_dir=data_dir,
        db_path=Path(os.environ.get("SMARTAI_DB", data_dir / "smartai.db")),
        snapshot_root=Path(os.environ.get("SMARTAI_SNAPSHOT_ROOT", data_dir / "snapshots")),
        use_mock_inference=os.environ.get("SMARTAI_MOCK_INFERENCE", "1") == "1",
        enable_relay_hardware=os.environ.get("SMARTAI_ENABLE_RELAY_HARDWARE", "0") == "1",
    )

