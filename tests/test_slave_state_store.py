"""Slave 本地状态存储测试。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from common.protocol import GameState
from slave.backend import SlaveBackend
from slave.state_store import SlaveStateStore


class TestSlaveStateStore:
    def test_load_settings_defaults_to_default_group(self, tmp_path: Path):
        store = SlaveStateStore(tmp_path)
        settings = store.load_settings()
        assert settings.group == "默认"

    def test_save_group_persists_settings(self, tmp_path: Path):
        store = SlaveStateStore(tmp_path)
        store.save_group("A组")

        data = json.loads((tmp_path / "slave_config.json").read_text(encoding="utf-8"))
        assert data["group"] == "A组"
        assert store.load_settings().group == "A组"

    def test_load_runtime_status_supports_desc_alias(self, tmp_path: Path):
        store = SlaveStateStore(tmp_path)
        (tmp_path / "runtime_status.json").write_text(
            json.dumps(
                {
                    "state": GameState.COMPLETED,
                    "level": "18",
                    "jin_bi": 9527,
                    "desc": "account-01",
                    "elapsed": "360",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        snapshot = store.load_runtime_status(default_elapsed="10")
        assert snapshot.state == GameState.COMPLETED
        assert snapshot.level == 18
        assert snapshot.jin_bi == "9527"
        assert snapshot.current_account == "account-01"
        assert snapshot.elapsed == "360"


class TestSlaveBackendStatusSnapshot:
    def test_runtime_snapshot_falls_back_to_running_state(self, tmp_path: Path):
        backend = SlaveBackend(tmp_path, None)
        backend._script_started_at = time.time() - 12
        (tmp_path / "runtime_status.json").write_text(
            json.dumps(
                {
                    "state": GameState.SCRIPT_STOPPED,
                    "level": 5,
                    "jin_bi": "88",
                    "current_account": "user-a",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        snapshot = backend._load_runtime_snapshot()
        assert snapshot.state == GameState.RUNNING
        assert snapshot.level == 5
        assert snapshot.jin_bi == "88"
        assert snapshot.current_account == "user-a"
        assert int(snapshot.elapsed) >= 10
