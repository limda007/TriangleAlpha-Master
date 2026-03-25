"""Slave 本地状态存储测试。"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from common.protocol import GameState
from slave.backend import SlaveBackend
from slave.state_store import RuntimeStatus, SlaveStateStore


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


    def test_load_runtime_status_supports_jinbi_key_alias(self, tmp_path: Path):
        """runtime_status.json 中 jinbi（无下划线）也能被正确解析。"""
        store = SlaveStateStore(tmp_path)
        (tmp_path / "runtime_status.json").write_text(
            json.dumps({"state": GameState.RUNNING, "level": 5, "jinbi": "6688", "current_account": "u1"}),
            encoding="utf-8",
        )
        snapshot = store.load_runtime_status()
        assert snapshot.jin_bi == "6688"

    def test_load_active_account_supports_JinBi_key(self, tmp_path: Path):
        """accounts.json 中 JinBi（无 Current 前缀）也能被解析。"""
        store = SlaveStateStore(tmp_path)
        (tmp_path / "accounts.json").write_text(
            json.dumps([{"Username": "u1", "CurrentLevel": 10, "JinBi": "7777", "IsActive": True}]),
            encoding="utf-8",
        )
        result = store.load_active_account(default_elapsed="0")
        assert result is not None
        assert result.jin_bi == "7777"

    def test_load_all_game_accounts_supports_JinBi_key(self, tmp_path: Path):
        """load_all_game_accounts 也支持 JinBi 别名。"""
        store = SlaveStateStore(tmp_path)
        (tmp_path / "accounts.json").write_text(
            json.dumps(
                [{
                    "Username": "u1",
                    "Password": "p1",
                    "CurrentLevel": 5,
                    "JinBi": "3333",
                    "IsBanned": False,
                    "IsActive": True,
                }],
            ),
            encoding="utf-8",
        )
        result = store.load_all_game_accounts()
        assert len(result) == 1
        assert result[0]["jin_bi"] == "3333"

    def test_load_active_account_from_accounts_json(self, tmp_path: Path):
        store = SlaveStateStore(tmp_path)
        (tmp_path / "accounts.json").write_text(
            json.dumps(
                [
                    {"Username": "111", "CurrentLevel": 18, "CurrentJinBi": "50000", "IsActive": False},
                    {"Username": "222", "CurrentLevel": 5, "CurrentJinBi": "1200", "IsActive": True},
                    {"Username": "333", "CurrentLevel": 10, "CurrentJinBi": "800", "IsActive": False},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = store.load_active_account(default_elapsed="60")
        assert result is not None
        assert result.current_account == "222"
        assert result.level == 5
        assert result.jin_bi == "1200"
        assert result.elapsed == "60"

    def test_load_active_account_returns_none_when_no_active(self, tmp_path: Path):
        store = SlaveStateStore(tmp_path)
        (tmp_path / "accounts.json").write_text(
            json.dumps([{"Username": "111", "IsActive": False}]),
            encoding="utf-8",
        )
        assert store.load_active_account() is None

    def test_load_active_account_returns_none_when_file_missing(self, tmp_path: Path):
        store = SlaveStateStore(tmp_path)
        assert store.load_active_account() is None

    def test_load_all_game_accounts(self, tmp_path: Path):
        """全量账号读取 → 统一格式映射"""
        store = SlaveStateStore(tmp_path)
        (tmp_path / "accounts.json").write_text(
            json.dumps(
                [
                    {
                        "Username": "user1", "Password": "p1",
                        "BindEmail": "e1@test.com", "BindEmailPassword": "ep1",
                        "CurrentLevel": 18, "CurrentJinBi": "50000",
                        "IsBanned": False, "IsActive": True,
                    },
                    {
                        "Username": "user2", "Password": "p2",
                        "CurrentLevel": 3, "CurrentJinBi": "100",
                        "IsBanned": True, "IsActive": False,
                    },
                    {
                        "Username": "", "Password": "empty",
                        "CurrentLevel": 0, "CurrentJinBi": "0",
                        "IsBanned": False, "IsActive": False,
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = store.load_all_game_accounts()
        assert len(result) == 2  # 空 username 被过滤
        assert result[0]["username"] == "user1"
        assert result[0]["password"] == "p1"
        assert result[0]["bind_email"] == "e1@test.com"
        assert result[0]["level"] == 18
        assert result[0]["jin_bi"] == "50000"
        assert result[0]["is_banned"] is False
        assert result[0]["is_active"] is True
        assert isinstance(result[0]["login_at"], str)
        assert result[0]["login_at"]
        assert result[1]["username"] == "user2"
        assert result[1]["is_banned"] is True

    def test_load_all_game_accounts_missing_file(self, tmp_path: Path):
        """文件不存在时返回空列表"""
        store = SlaveStateStore(tmp_path)
        assert store.load_all_game_accounts() == []

    def test_load_all_game_accounts_records_login_time_for_new_active_account(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        store = SlaveStateStore(tmp_path)
        monkeypatch.setattr(
            SlaveStateStore,
            "_now_text",
            staticmethod(lambda: "2026-03-24 10:00:00"),
        )
        (tmp_path / "accounts.json").write_text(
            json.dumps([{"Username": "u1", "IsActive": True}], ensure_ascii=False),
            encoding="utf-8",
        )

        result = store.load_all_game_accounts()

        assert result[0]["login_at"] == "2026-03-24 10:00:00"
        state = json.loads((tmp_path / "account_login_state.json").read_text(encoding="utf-8"))
        assert state["u1"]["last_login_at"] == "2026-03-24 10:00:00"
        assert state["u1"]["was_active"] is True

    def test_load_all_game_accounts_keeps_login_time_while_account_stays_active(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        store = SlaveStateStore(tmp_path)
        (tmp_path / "account_login_state.json").write_text(
            json.dumps(
                {"u1": {"last_login_at": "2026-03-24 10:00:00", "was_active": True}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            SlaveStateStore,
            "_now_text",
            staticmethod(lambda: "2026-03-24 11:30:00"),
        )
        (tmp_path / "accounts.json").write_text(
            json.dumps([{"Username": "u1", "IsActive": True}], ensure_ascii=False),
            encoding="utf-8",
        )

        result = store.load_all_game_accounts()

        assert result[0]["login_at"] == "2026-03-24 10:00:00"

    def test_load_all_game_accounts_updates_login_time_on_relogin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        store = SlaveStateStore(tmp_path)
        (tmp_path / "account_login_state.json").write_text(
            json.dumps(
                {"u1": {"last_login_at": "2026-03-24 10:00:00", "was_active": False}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            SlaveStateStore,
            "_now_text",
            staticmethod(lambda: "2026-03-24 12:15:00"),
        )
        (tmp_path / "accounts.json").write_text(
            json.dumps([{"Username": "u1", "IsActive": True}], ensure_ascii=False),
            encoding="utf-8",
        )

        result = store.load_all_game_accounts()

        assert result[0]["login_at"] == "2026-03-24 12:15:00"


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
        assert snapshot.current_account == ""
        assert int(snapshot.elapsed) >= 10

    def test_runtime_snapshot_falls_back_to_accounts_json(self, tmp_path: Path):
        """runtime_status.json 不存在时，从 accounts.json 读取活跃账号。"""
        backend = SlaveBackend(tmp_path, None)
        backend._script_started_at = time.time() - 30
        (tmp_path / "accounts.json").write_text(
            json.dumps(
                [
                    {"Username": "idle-acc", "CurrentLevel": 18, "CurrentJinBi": "50000", "IsActive": False},
                    {"Username": "active-acc", "CurrentLevel": 7, "CurrentJinBi": "3500", "IsActive": True},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        snapshot = backend._load_runtime_snapshot()
        assert snapshot.state == GameState.RUNNING
        assert snapshot.current_account == "active-acc"
        assert snapshot.level == 7
        assert snapshot.jin_bi == "3500"
        assert int(snapshot.elapsed) >= 28

    def test_account_sync_aligns_login_time_with_elapsed(self, tmp_path: Path):
        backend = SlaveBackend(tmp_path, None)
        (tmp_path / "accounts.json").write_text(
            json.dumps(
                [
                    {"Username": "user1", "IsActive": True},
                    {"Username": "user2", "IsActive": False},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (tmp_path / "account_login_state.json").write_text(
            json.dumps(
                {"user1": {"last_login_at": "2026-03-24 17:31:00", "was_active": True}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        backend._load_runtime_snapshot = lambda: RuntimeStatus(  # type: ignore[method-assign]
            current_account="user1",
            elapsed="4h56m",
        )
        accounts = backend._build_account_sync_accounts(now=datetime(2026, 3, 24, 19, 0, 0))

        assert accounts[0]["login_at"] == "2026-03-24 14:04:00"

    def test_parse_elapsed_seconds_supports_human_readable_text(self):
        assert SlaveBackend._parse_elapsed_seconds("4h56m") == 17760


class TestMapIpcStatus:
    """IPC status_text → GameState 映射。"""

    def test_running_status(self):
        from slave.backend import SlaveBackend
        assert SlaveBackend._map_ipc_status("运行中") == GameState.RUNNING

    def test_completed_status(self):
        from slave.backend import SlaveBackend
        assert SlaveBackend._map_ipc_status("已完成") == GameState.COMPLETED

    def test_intermediate_completion_stays_running(self):
        """'完成过关'等中间状态不应被误判为账号已完成"""
        from slave.backend import SlaveBackend
        assert SlaveBackend._map_ipc_status("完成过关") == GameState.RUNNING

    def test_stopped_status(self):
        from slave.backend import SlaveBackend
        assert SlaveBackend._map_ipc_status("脚本已停止") == GameState.SCRIPT_STOPPED
        assert SlaveBackend._map_ipc_status("已退出") == GameState.SCRIPT_STOPPED

    def test_empty_defaults_to_running(self):
        from slave.backend import SlaveBackend
        assert SlaveBackend._map_ipc_status("") == GameState.RUNNING

    def test_unknown_text_defaults_to_running(self):
        from slave.backend import SlaveBackend
        assert SlaveBackend._map_ipc_status("过关中") == GameState.RUNNING
        assert SlaveBackend._map_ipc_status("等待指令") == GameState.RUNNING


class TestIpcPriorityInSnapshot:
    """_load_runtime_snapshot 中 IPC 优先级测试。"""

    def test_ipc_data_takes_priority_over_file(self, tmp_path: Path):
        """IPC 有效时忽略文件。"""
        from slave.backend import SlaveBackend
        from slave.ipc_receiver import LocalIpcReceiver

        backend = SlaveBackend(tmp_path, None)
        backend._script_started_at = time.time() - 30

        (tmp_path / "runtime_status.json").write_text(
            json.dumps({"state": GameState.RUNNING, "level": 1, "jin_bi": "10",
                         "current_account": "file-acc", "elapsed": "5"}),
            encoding="utf-8",
        )

        backend._ipc = LocalIpcReceiver()
        backend._ipc._on_message({
            "account": "ipc-acc", "level": "20",
            "jinbi": "9999", "status_text": "运行中", "elapsed": "100",
        })

        snapshot = backend._load_runtime_snapshot()
        assert snapshot.current_account == "ipc-acc"
        assert snapshot.level == 20
        assert snapshot.jin_bi == "9999"
        assert snapshot.elapsed == "100"

    def test_stale_ipc_falls_back_to_file(self, tmp_path: Path):
        """IPC 过期（>15s）时回退到文件。"""
        from slave.backend import SlaveBackend
        from slave.ipc_receiver import LocalIpcReceiver

        backend = SlaveBackend(tmp_path, None)
        backend._script_started_at = time.time() - 30

        (tmp_path / "runtime_status.json").write_text(
            json.dumps({"state": GameState.RUNNING, "level": 5, "jin_bi": "200",
                         "current_account": "file-acc", "elapsed": "50"}),
            encoding="utf-8",
        )

        backend._ipc = LocalIpcReceiver()
        backend._ipc._data = {
            "account": "old-ipc", "level": "10",
            "jinbi": "500", "status_text": "运行中", "elapsed": "30",
        }
        backend._ipc._last_sync = time.monotonic() - 20  # 过期 20s

        snapshot = backend._load_runtime_snapshot()
        assert snapshot.current_account == ""
        assert snapshot.level == 5

    def test_no_ipc_uses_file(self, tmp_path: Path):
        """无 IPC 数据时正常使用文件。"""
        from slave.backend import SlaveBackend
        from slave.ipc_receiver import LocalIpcReceiver

        backend = SlaveBackend(tmp_path, None)
        backend._script_started_at = time.time() - 10

        (tmp_path / "runtime_status.json").write_text(
            json.dumps({"state": GameState.RUNNING, "level": 8, "jin_bi": "600",
                         "current_account": "file-only"}),
            encoding="utf-8",
        )

        backend._ipc = LocalIpcReceiver()

        snapshot = backend._load_runtime_snapshot()
        assert snapshot.current_account == ""
        assert snapshot.jin_bi == "600"

    def test_on_account_updated_clears_stale_runtime_state(self, tmp_path: Path):
        backend = SlaveBackend(tmp_path, None)
        (tmp_path / "runtime_status.json").write_text(
            json.dumps({"current_account": "stale-user"}),
            encoding="utf-8",
        )
        backend._last_ipc_jin_bi = "9999"
        backend._ipc._on_message({
            "account": "stale-user",
            "level": "12",
            "jinbi": "9999",
            "status_text": "运行中",
            "elapsed": "30",
        })

        backend._on_account_updated(2)

        assert not (tmp_path / "runtime_status.json").exists()
        data, age = backend._ipc.snapshot()
        assert data is None
        assert age == float("inf")
        assert backend._last_ipc_jin_bi == "0"


class TestNeedAccountRetry:
    def test_waiting_for_account_detects_requesting_state(self, tmp_path: Path):
        backend = SlaveBackend(tmp_path, None)

        assert backend._is_waiting_for_account(
            RuntimeStatus(
                state=GameState.RUNNING,
                current_account="",
                status_text="本地无可用账号，正在向中控申请...",
            )
        )

    def test_waiting_for_account_requires_empty_current_account(self, tmp_path: Path):
        backend = SlaveBackend(tmp_path, None)

        assert not backend._is_waiting_for_account(
            RuntimeStatus(
                state=GameState.RUNNING,
                current_account="user-a",
                status_text="本地无可用账号，正在向中控申请...",
            )
        )

    def test_retry_need_account_respects_cooldown(self, tmp_path: Path):
        backend = SlaveBackend(tmp_path, None)
        heartbeat = MagicMock()
        snapshot = RuntimeStatus(
            state=GameState.RUNNING,
            current_account="",
            status_text="本地无可用账号，正在向中控申请...",
        )

        assert backend._retry_need_account_if_needed(snapshot, heartbeat, now_monotonic=100.0)
        assert not backend._retry_need_account_if_needed(snapshot, heartbeat, now_monotonic=105.0)
        assert backend._retry_need_account_if_needed(snapshot, heartbeat, now_monotonic=116.0)
        assert heartbeat.send_need_account.call_count == 2

    def test_retry_need_account_resets_after_recovery(self, tmp_path: Path):
        backend = SlaveBackend(tmp_path, None)
        heartbeat = MagicMock()
        waiting = RuntimeStatus(
            state=GameState.RUNNING,
            current_account="",
            status_text="本地无可用账号，正在向中控申请...",
        )
        running = RuntimeStatus(
            state=GameState.RUNNING,
            current_account="user-a",
            status_text="运行中",
        )

        assert backend._retry_need_account_if_needed(waiting, heartbeat, now_monotonic=100.0)
        assert not backend._retry_need_account_if_needed(running, heartbeat, now_monotonic=101.0)
        assert backend._retry_need_account_if_needed(waiting, heartbeat, now_monotonic=102.0)
        assert heartbeat.send_need_account.call_count == 2
