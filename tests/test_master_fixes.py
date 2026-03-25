"""Master 核心组件联调测试 — 验证所有修复项"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from common.models import PLATFORM_ACCOUNT_HEADER, AccountInfo, AccountStatus, NodeInfo
from common.protocol import ACCOUNT_RUNTIME_CLEANUP_PAYLOAD, GameState, TcpCommand
from master.app.core.account_db import AccountDB
from master.app.core.kami_db import KamiDB
from master.app.core.node_manager import NodeManager
from master.app.core.platform_syncer import PlatformSyncer

# ── C1: themeMode 配置项 ──


class TestC1ThemeMode:
    """验证 config.py 中添加了 themeMode"""

    def test_theme_mode_exists(self):
        from master.app.common.config import cfg
        assert hasattr(cfg, "themeMode"), "cfg 应有 themeMode 属性"

    def test_theme_mode_default_is_auto(self):
        from qfluentwidgets import Theme

        from master.app.common.config import cfg
        assert cfg.themeMode.defaultValue == Theme.AUTO

    def test_theme_changed_signal_exists(self):
        from master.app.common.config import cfg
        assert hasattr(cfg, "themeChanged"), "QConfig 基类应提供 themeChanged 信号"


# ── C2: 文件操作异常处理 ──


class TestC2FileErrorHandling:
    """验证 AccountDB.load_from_file 对不存在文件抛出 OSError"""

    def test_load_from_nonexistent_file(self, tmp_path):
        pool = AccountDB(tmp_path / "test.db")
        with pytest.raises(OSError, match="无法读取账号文件"):
            pool.load_from_file(tmp_path / "not_exist.txt")
        pool.close()

    def test_load_from_valid_file(self, tmp_path):
        f = tmp_path / "accounts.txt"
        f.write_text("user1----pass1\nuser2----pass2", encoding="utf-8")
        pool = AccountDB(tmp_path / "test.db")
        pool.load_from_file(f)
        assert pool.total_count == 2
        pool.close()


# ── C3: TCP socket 关闭 ──


class TestC3TcpSocketClose:
    """验证 _TcpSendTask 在异常时也关闭 socket"""

    def test_socket_closed_on_connect_failure(self):
        from master.app.core.tcp_commander import TcpCommander, _TcpSendTask

        commander = MagicMock(spec=TcpCommander)
        commander.command_failed = MagicMock()
        commander.command_sent = MagicMock()
        task = _TcpSendTask("1.2.3.4", "STARTEXE|", commander)

        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("refused")

        with patch("master.app.core.tcp_commander.socket.socket", return_value=mock_sock):
            task.run()

        # socket 必须被关闭
        mock_sock.close.assert_called_once()
        # 应发射 command_failed 信号
        commander.command_failed.emit.assert_called_once()

    def test_socket_closed_on_success(self):
        from master.app.core.tcp_commander import TcpCommander, _TcpSendTask

        commander = MagicMock(spec=TcpCommander)
        commander.command_sent = MagicMock()
        task = _TcpSendTask("1.2.3.4", "STARTEXE|", commander)

        mock_sock = MagicMock()
        with patch("master.app.core.tcp_commander.socket.socket", return_value=mock_sock):
            task.run()

        mock_sock.close.assert_called_once()
        commander.command_sent.emit.assert_called_once()

    def test_self_update_broken_pipe_is_treated_as_expected(self):
        from master.app.core.tcp_commander import TcpCommander, _TcpSendTask

        commander = MagicMock(spec=TcpCommander)
        commander.command_failed = MagicMock()
        commander.command_sent = MagicMock()
        task = _TcpSendTask("1.2.3.4", "UPDATESELF|payload", commander)

        mock_sock = MagicMock()
        mock_sock.sendall.side_effect = BrokenPipeError(32, "Broken pipe")

        with patch("master.app.core.tcp_commander.socket.socket", return_value=mock_sock):
            task.run()

        mock_sock.close.assert_called_once()
        commander.command_failed.emit.assert_not_called()
        commander.command_sent.emit.assert_called_once()


# ── H3: 操作历史动态过滤基础 ──


class TestH3HistoryRecords:
    """验证 add_history 后记录正确，为动态过滤提供基础"""

    def test_add_history_records(self):
        nm = NodeManager()
        nm.add_history("启动脚本", "3 个节点")
        nm.add_history("停止脚本", "2 个节点")
        nm.add_history("启动脚本", "5 个节点")

        assert len(nm.history) == 3
        types = {r.op_type for r in nm.history}
        assert types == {"启动脚本", "停止脚本"}


# ── H7: EXT_QUERY 已移除 ──


class TestH7ExtQueryRemoved:
    """验证 TcpCommand 不再有 EXT_QUERY"""

    def test_no_ext_query(self):
        assert not hasattr(TcpCommand, "EXT_QUERY")

    def test_ext_set_group_still_exists(self):
        assert hasattr(TcpCommand, "EXT_SET_GROUP")


# ── H8: LogReceiver 解析格式 ──


class TestH8LogReceiverParsing:
    """验证 LogReceiverThread._parse_line 格式处理"""

    def test_parse_valid_log(self):
        from master.app.core.log_receiver import LogReceiverThread

        receiver = LogReceiverThread(port=0)
        entries = []
        receiver.log_received.connect(entries.append)

        receiver._parse_line("LOG|VM-01|12:30:45|INFO|启动成功")
        assert len(entries) == 1
        assert entries[0].machine_name == "VM-01"
        assert entries[0].level == "INFO"
        assert entries[0].content == "启动成功"

    def test_parse_ignores_non_log(self):
        from master.app.core.log_receiver import LogReceiverThread

        receiver = LogReceiverThread(port=0)
        entries = []
        receiver.log_received.connect(entries.append)

        receiver._parse_line("HEARTBEAT|VM-01|alive")
        assert len(entries) == 0


# ── M1: history_changed 信号 ──


class TestM1HistoryChangedSignal:
    """验证 NodeManager.add_history 发射 history_changed 信号"""

    def test_signal_emitted(self):
        nm = NodeManager()
        received = []
        nm.history_changed.connect(lambda: received.append(True))

        nm.add_history("测试操作", "目标")

        assert len(received) == 1

    def test_signal_emitted_multiple_times(self):
        nm = NodeManager()
        count = []
        nm.history_changed.connect(lambda: count.append(1))

        nm.add_history("操作1", "目标1")
        nm.add_history("操作2", "目标2")
        nm.add_history("操作3", "目标3")

        assert len(count) == 3


# ── M2: signal_bus.py 已删除 ──


class TestM2SignalBusDeleted:
    """验证 signal_bus.py 不再存在"""

    def test_file_not_exists(self):
        path = Path(__file__).parent.parent / "src" / "master" / "app" / "common" / "signal_bus.py"
        assert not path.exists(), f"signal_bus.py 应已删除: {path}"


class TestKamiManualOnly:
    """验证卡密改为仅手动分配。"""

    def test_node_manager_need_kami_signal_removed(self):
        nm = NodeManager()
        assert not hasattr(nm, "need_kami")

    def test_main_window_auto_kami_hooks_removed(self):
        from master.app.view.main_window import MainWindow

        assert not hasattr(MainWindow, "_autoAssignKami")
        assert not hasattr(MainWindow, "_onNeedKami")


class TestKamiBindingGuards:
    """验证手动分配路径的卡密绑定保护。"""

    def test_bind_node_rejects_second_kami_for_same_node(self, tmp_path):
        db = KamiDB(tmp_path / "kami.db")
        db.upsert_kamis([
            {"kami": "KAMI-001", "ok": True, "status": "已激活", "device_count": "0/1"},
            {"kami": "KAMI-002", "ok": True, "status": "已激活", "device_count": "0/1"},
        ])
        kamis = db.get_all_kamis()

        assert db.bind_node(kamis[0].id, "VM-01") is True
        assert db.bind_node(kamis[1].id, "VM-01") is False
        current = db.get_kami_for_node("VM-01")
        assert current is not None
        assert current.kami_code == "KAMI-001"
        db.close()

    def test_bind_node_rejects_full_kami(self, tmp_path):
        db = KamiDB(tmp_path / "kami.db")
        db.upsert_kamis([
            {"kami": "KAMI-FULL", "ok": True, "status": "已激活", "device_count": "1/1"},
        ])
        kami = db.get_all_kamis()[0]

        assert db.bind_node(kami.id, "VM-01") is False
        assert db.get_kami_for_node("VM-01") is None
        db.close()


    def test_upsert_sets_fallback_device_total_for_activated_kami(self, tmp_path):
        db = KamiDB(tmp_path / "kami.db")
        db.upsert_kamis([
            {"kami": "KAMI-ZERO", "ok": True, "status": "已激活", "device_count": "0"},
        ])

        kami = db.get_all_kamis()[0]
        assert kami.device_total == 1
        assert db.find_available_kami() is not None
        db.close()


# ── M7: 导出时间戳 ──


class TestM7ExportTimestamp:
    """验证 export_completed 包含完成时间"""

    def test_export_includes_timestamp(self, tmp_path):
        pool = AccountDB(tmp_path / "test.db")
        pool.import_fresh("user1----pass1\nuser2----pass2")
        pool.allocate("VM-01")
        pool.complete("VM-01", level=30)
        # 直接 SQL 设置精确时间戳（complete() 使用 datetime.now()）
        pool._conn.execute(
            "UPDATE accounts SET last_login_at='2026-03-18 10:30:00', "
            "completed_at='2026-03-18 14:30:00' WHERE username='user1'"
        )
        pool._conn.commit()

        result = pool.export_completed()
        assert "2026-03-18 14:30:00" in result
        assert "2026-03-18 10:30:00" in result
        assert "----30----" in result
        pool.close()

    def test_export_without_completed_at(self, tmp_path):
        pool = AccountDB(tmp_path / "test.db")
        pool.import_fresh("user1----pass1")
        pool.allocate("VM-01")
        # 直接 SQL 设置已完成但无时间
        pool._conn.execute(
            "UPDATE accounts SET status='已完成', completed_at=NULL WHERE username='user1'"
        )
        pool._conn.commit()
        pool._refresh_counts()

        result = pool.export_completed()
        assert "----无" in result
        pool.close()


class TestPlatformUploadFormat:
    """验证销售平台上传文本采用表头 + 10 字段数据行格式"""

    def test_build_upload_text_matches_sales_platform_format(self) -> None:
        account = AccountInfo(
            username="754047983304983",
            password="xozo9LLEYKXEe",
            bind_email="AustinHill9146@outlook.com",
            bind_email_password="afxgc1070",
            status=AccountStatus.COMPLETED,
            level=18,
            jin_bi="2390K",
            notes="无",
            last_login_at=datetime(2026, 3, 24, 10, 8, 39),
            completed_at=datetime(2026, 3, 24, 13, 8, 39),
        )

        result = PlatformSyncer._build_upload_text([account])

        assert result == (
            f"{PLATFORM_ACCOUNT_HEADER}\n"
            "754047983304983----xozo9LLEYKXEe----AustinHill9146@outlook.com----"
            "afxgc1070----18----2390K----正常----无----2026-03-24 10:08:39----"
            "2026-03-24 13:08:39"
        )


class _FakeSignal:
    def __init__(self) -> None:
        self._slots: list[object] = []

    def connect(self, slot: object) -> None:
        self._slots.append(slot)


class _FakeSyncWorker:
    instances: list[_FakeSyncWorker] = []

    def __init__(
        self,
        _client_cfg: object,
        task: str,
        upload_text: str = "",
        group_name: str = "",
        upload_usernames: list[str] | None = None,
        parent: object | None = None,
    ) -> None:
        self.task = task
        self.upload_text = upload_text
        self.group_name = group_name
        self.upload_usernames = upload_usernames or []
        self.parent = parent
        self.upload_done = _FakeSignal()
        self.poll_done = _FakeSignal()
        self.tokens_updated = _FakeSignal()
        self.error_occurred = _FakeSignal()
        self.finished = _FakeSignal()
        self._running = False
        self.__class__.instances.append(self)

    def start(self) -> None:
        self._running = True

    def isRunning(self) -> bool:
        return self._running

    def quit(self) -> None:
        self._running = False

    def wait(self, _timeout: int) -> bool:
        return True

    def deleteLater(self) -> None:
        return None


class TestPlatformUploadBatching:
    """验证平台同步一次只上传 200 个账号，避免大批量卡死。"""

    def test_try_upload_completed_limits_batch_size(self, tmp_path, monkeypatch) -> None:
        import master.app.core.platform_syncer as platform_syncer_module

        db = AccountDB(tmp_path / "test.db")
        try:
            lines = "\n".join(f"u{i}----p{i}" for i in range(205))
            db.import_fresh(lines)
            db._conn.execute(
                "UPDATE accounts SET status='已完成', completed_at='2026-03-25 10:00:00'"
            )
            db._conn.commit()
            db._refresh_counts()

            _FakeSyncWorker.instances.clear()
            monkeypatch.setattr(platform_syncer_module, "_SyncWorker", _FakeSyncWorker)

            syncer = PlatformSyncer(db)
            syncer._enabled = True
            syncer._api_url = "http://example.com"
            syncer._group_name = "group-a"

            syncer.try_upload_completed()

            assert len(_FakeSyncWorker.instances) == 1
            worker = _FakeSyncWorker.instances[0]
            assert worker.task == "upload"
            assert worker.group_name == "group-a"
            assert len(worker.upload_usernames) == 200
            assert worker.upload_usernames[0] == "u0"
            assert worker.upload_usernames[-1] == "u199"
            assert len(worker.upload_text.splitlines()) == 201
        finally:
            db.close()


class TestPlatformRetryBackoff:
    """验证平台同步的退避重试与漏补自检。"""

    def test_worker_error_schedules_exponential_backoff(self, tmp_path) -> None:
        db = AccountDB(tmp_path / "test.db")
        try:
            syncer = PlatformSyncer(db)
            syncer._enabled = True
            syncer._api_url = "http://example.com"
            syncer._backoff_timer.start = MagicMock()  # type: ignore[method-assign]

            syncer._on_worker_error("请求失败：ReadTimeout")
            syncer._on_worker_error("上传失败(429)：too many requests")

            assert syncer._backoff_timer.start.call_args_list == [((20_000,), {}), ((40_000,), {})]
            assert syncer._next_backoff_ms == 80_000
        finally:
            db.close()

    def test_success_resets_backoff_window(self, tmp_path) -> None:
        db = AccountDB(tmp_path / "test.db")
        try:
            syncer = PlatformSyncer(db)
            syncer._enabled = True
            syncer._api_url = "http://example.com"
            syncer._backoff_timer.start = MagicMock()  # type: ignore[method-assign]
            syncer._backoff_timer.stop = MagicMock()  # type: ignore[method-assign]

            syncer._on_worker_error("请求失败：ReadTimeout")
            assert syncer._next_backoff_ms == 40_000

            syncer._on_tokens_updated("at_ok", "rt_ok")

            syncer._backoff_timer.stop.assert_called()
            assert syncer._next_backoff_ms == 20_000
        finally:
            db.close()

    def test_poll_empty_result_still_triggers_pending_upload_self_check(self, tmp_path, monkeypatch) -> None:
        import master.app.core.platform_syncer as platform_syncer_module

        db = AccountDB(tmp_path / "test.db")
        try:
            db.import_fresh("u1----p1")
            db._conn.execute(
                "UPDATE accounts SET status='已完成', completed_at='2026-03-25 10:00:00'"
            )
            db._conn.commit()
            db._refresh_counts()

            _FakeSyncWorker.instances.clear()
            monkeypatch.setattr(platform_syncer_module, "_SyncWorker", _FakeSyncWorker)
            scheduled: list[Callable[[], None]] = []
            monkeypatch.setattr(
                platform_syncer_module.QTimer,
                "singleShot",
                staticmethod(lambda _ms, callback: scheduled.append(callback)),
            )

            syncer = PlatformSyncer(db)
            syncer._enabled = True
            syncer._api_url = "http://example.com"
            syncer._group_name = "group-a"

            poll_worker = _FakeSyncWorker(syncer._client, "poll")
            syncer._worker = poll_worker  # type: ignore[assignment]

            syncer._on_poll_done([])
            syncer._cleanup_worker()

            assert len(scheduled) == 1
            callback = scheduled.pop()
            callback()

            assert _FakeSyncWorker.instances[-1].task == "upload"
            assert _FakeSyncWorker.instances[-1].upload_usernames == ["u1"]
        finally:
            db.close()

    def test_poll_username_field_is_supported(self) -> None:
        from master.app.core.platform_syncer import _SyncWorker

        result = _SyncWorker._extract_taken_usernames([
            {"username": "u1", "status": "taken"},
            {"steam_account": "u2", "status": "taken"},
            {"username": "", "steam_account": ""},
        ])

        assert result == ["u1", "u2"]

    def test_pending_upload_resumes_after_other_worker_finishes(self, tmp_path, monkeypatch) -> None:
        import master.app.core.platform_syncer as platform_syncer_module

        db = AccountDB(tmp_path / "test.db")
        try:
            lines = "\n".join(f"u{i}----p{i}" for i in range(205))
            db.import_fresh(lines)
            db._conn.execute(
                "UPDATE accounts SET status='已完成', completed_at='2026-03-25 10:00:00'"
            )
            db._conn.commit()
            db._refresh_counts()

            _FakeSyncWorker.instances.clear()
            monkeypatch.setattr(platform_syncer_module, "_SyncWorker", _FakeSyncWorker)
            scheduled: list[Callable[[], None]] = []
            monkeypatch.setattr(
                platform_syncer_module.QTimer,
                "singleShot",
                staticmethod(lambda _ms, callback: scheduled.append(callback)),
            )

            syncer = PlatformSyncer(db)
            syncer._enabled = True
            syncer._api_url = "http://example.com"
            syncer._group_name = "group-a"

            first_batch = [f"u{i}" for i in range(200)]
            upload_worker = _FakeSyncWorker(syncer._client, "upload", upload_usernames=first_batch)
            syncer._worker = upload_worker  # type: ignore[assignment]

            syncer._on_upload_done(first_batch)
            syncer._cleanup_worker()

            assert len(scheduled) == 1

            blocking_worker = _FakeSyncWorker(syncer._client, "poll")
            blocking_worker._running = True
            syncer._worker = blocking_worker  # type: ignore[assignment]

            callback = scheduled.pop()
            callback()

            assert syncer._upload_dirty is True
            assert syncer._resume_upload_after_worker is True

            blocking_worker._running = False
            syncer._cleanup_worker()

            assert len(scheduled) == 1

            callback = scheduled.pop()
            callback()

            assert len(_FakeSyncWorker.instances[-1].upload_usernames) == 5
            assert _FakeSyncWorker.instances[-1].upload_usernames == [f"u{i}" for i in range(200, 205)]
        finally:
            db.close()


# ── M10-fix: history 上限 ──


class TestHistoryCap:
    """验证 NodeManager.history 不会无限增长"""

    def test_history_capped_at_max(self):
        nm = NodeManager()
        for i in range(1200):
            nm.add_history("操作", f"目标-{i}")
        assert len(nm.history) <= 1000

    def test_history_preserves_recent(self):
        nm = NodeManager()
        for i in range(1100):
            nm.add_history("操作", f"目标-{i}")
        # 最新的应保留
        assert nm.history[-1].target == "目标-1099"
        # 最早的应被丢弃
        targets = {r.target for r in nm.history}
        assert "目标-0" not in targets


# ── M11-fix: TcpCommander.stop() ──


class TestTcpCommanderShutdown:
    """验证 TcpCommander 有 stop() 方法并能关闭线程池"""

    def test_stop_method_exists(self):
        from master.app.core.tcp_commander import TcpCommander

        commander = TcpCommander()
        assert hasattr(commander, "stop"), "TcpCommander 应有 stop() 方法"

    def test_stop_waits_for_pool(self):
        from master.app.core.tcp_commander import TcpCommander

        commander = TcpCommander()
        mock_pool = MagicMock()
        commander._pool = mock_pool

        commander.stop()

        mock_pool.waitForDone.assert_called_once()


class TestCompletedStatusCleanup:
    """验证 master 在确认已完成后只下发一次 slave 清理指令。"""

    def test_sync_completed_status_requests_slave_cleanup_once(self, tmp_path) -> None:
        from master.app.view.main_window import MainWindow

        db = AccountDB(tmp_path / "test.db")
        try:
            db.import_fresh("u1----p1")
            db.allocate("VM-01")

            fake = type("FakeMainWindow", (), {})()
            fake.nodeManager = NodeManager()
            fake.accountPool = db
            fake.tcpCommander = MagicMock()
            fake._completed_cleanup_sent = {}
            fake._request_slave_account_cleanup = MainWindow._request_slave_account_cleanup.__get__(
                fake, type(fake)
            )
            fake.nodeManager.nodes["VM-01"] = NodeInfo(
                machine_name="VM-01",
                ip="10.0.0.1",
                level=18,
                jin_bi="600",
                elapsed="120",
                current_account="u1",
                game_state=GameState.COMPLETED,
            )

            MainWindow._syncAccountFromNode(fake, "VM-01")
            MainWindow._syncAccountFromNode(fake, "VM-01")

            fake.tcpCommander.send.assert_called_once_with(
                "10.0.0.1",
                TcpCommand.DELETE_FILE,
                ACCOUNT_RUNTIME_CLEANUP_PAYLOAD,
            )
        finally:
            db.close()


class TestMissedNeedAccountRemediation:
    """验证漏收 NEED_ACCOUNT 后，主控能基于状态文案补发账号。"""

    def test_waiting_status_triggers_account_remediation(self, tmp_path) -> None:
        from master.app.view.main_window import MainWindow

        db = AccountDB(tmp_path / "test.db")
        try:
            db.import_fresh("u1----p1")
            expected_payload = AccountInfo.from_line("u1----p1").to_line()

            fake = type("FakeMainWindow", (), {})()
            fake.nodeManager = NodeManager()
            fake.accountPool = db
            fake.tcpCommander = MagicMock()
            fake._completed_cleanup_sent = {}
            fake._account_retry_sent_at = {}
            fake._ACCOUNT_RETRY_SEC = 10
            fake._onNeedAccount = MainWindow._onNeedAccount.__get__(fake, type(fake))
            fake._needs_account_remediation = MainWindow._needs_account_remediation.__get__(
                fake, type(fake)
            )
            fake._retry_missed_account_request = MainWindow._retry_missed_account_request.__get__(
                fake, type(fake)
            )

            fake.nodeManager.nodes["VM-01"] = NodeInfo(
                machine_name="VM-01",
                ip="10.0.0.1",
                game_state=GameState.RUNNING,
                status_text="本地无可用账号，正在向中控申请...",
            )

            with patch("master.app.view.main_window.time.monotonic", return_value=100.0):
                MainWindow._retry_missed_account_request(fake, "VM-01")

            fake.tcpCommander.send.assert_called_once_with(
                "10.0.0.1",
                TcpCommand.UPDATE_TXT,
                expected_payload,
            )
            bound = db.get_account_for_machine("VM-01")
            assert bound is not None
            assert bound.username == "u1"
        finally:
            db.close()

    def test_waiting_status_is_throttled_but_retries_after_cooldown(self, tmp_path) -> None:
        from master.app.view.main_window import MainWindow

        db = AccountDB(tmp_path / "test.db")
        try:
            db.import_fresh("u1----p1")
            expected_payload = AccountInfo.from_line("u1----p1").to_line()

            fake = type("FakeMainWindow", (), {})()
            fake.nodeManager = NodeManager()
            fake.accountPool = db
            fake.tcpCommander = MagicMock()
            fake._completed_cleanup_sent = {}
            fake._account_retry_sent_at = {}
            fake._ACCOUNT_RETRY_SEC = 10
            fake._onNeedAccount = MainWindow._onNeedAccount.__get__(fake, type(fake))
            fake._needs_account_remediation = MainWindow._needs_account_remediation.__get__(
                fake, type(fake)
            )
            fake._retry_missed_account_request = MainWindow._retry_missed_account_request.__get__(
                fake, type(fake)
            )

            fake.nodeManager.nodes["VM-01"] = NodeInfo(
                machine_name="VM-01",
                ip="10.0.0.1",
                game_state=GameState.RUNNING,
                status_text="本地无可用账号，正在向中控申请...",
            )

            with patch("master.app.view.main_window.time.monotonic", side_effect=[100.0, 100.0, 111.0]):
                MainWindow._retry_missed_account_request(fake, "VM-01")
                MainWindow._retry_missed_account_request(fake, "VM-01")
                MainWindow._retry_missed_account_request(fake, "VM-01")

            assert fake.tcpCommander.send.call_count == 2
            fake.tcpCommander.send.assert_any_call(
                "10.0.0.1",
                TcpCommand.UPDATE_TXT,
                expected_payload,
            )
        finally:
            db.close()

    def test_waiting_status_with_stale_current_account_still_retries(self, tmp_path) -> None:
        from master.app.view.main_window import MainWindow

        db = AccountDB(tmp_path / "test.db")
        try:
            db.import_fresh("u1----p1")
            expected_payload = AccountInfo.from_line("u1----p1").to_line()

            fake = type("FakeMainWindow", (), {})()
            fake.nodeManager = NodeManager()
            fake.accountPool = db
            fake.tcpCommander = MagicMock()
            fake._completed_cleanup_sent = {}
            fake._account_retry_sent_at = {}
            fake._ACCOUNT_RETRY_SEC = 10
            fake._onNeedAccount = MainWindow._onNeedAccount.__get__(fake, type(fake))
            fake._needs_account_remediation = MainWindow._needs_account_remediation.__get__(
                fake, type(fake)
            )
            fake._retry_missed_account_request = MainWindow._retry_missed_account_request.__get__(
                fake, type(fake)
            )

            fake.nodeManager.nodes["VM-01"] = NodeInfo(
                machine_name="VM-01",
                ip="10.0.0.1",
                current_account="old-user",
                game_state=GameState.RUNNING,
                status_text="本地无可用账号，正在向中控申请...",
            )

            with patch("master.app.view.main_window.time.monotonic", return_value=100.0):
                MainWindow._retry_missed_account_request(fake, "VM-01")

            fake.tcpCommander.send.assert_called_once_with(
                "10.0.0.1",
                TcpCommand.UPDATE_TXT,
                expected_payload,
            )
        finally:
            db.close()


# ── M1-fix: LogReceiver 并发处理连接 ──


class TestLogReceiverConcurrency:
    """验证 LogReceiverThread 使用线程池并发处理连接"""

    def test_has_executor_for_concurrent_handling(self):
        from master.app.core.log_receiver import LogReceiverThread

        receiver = LogReceiverThread(port=0)
        assert hasattr(receiver, "_executor"), "应有 ThreadPoolExecutor"

    def test_handle_conn_parses_data(self):
        """验证 _handle_conn 正确解析单个连接的数据"""
        import socket

        from master.app.core.log_receiver import LogReceiverThread

        receiver = LogReceiverThread(port=0)
        entries = []
        receiver.log_received.connect(entries.append)

        # 用 socketpair 模拟连接
        s1, s2 = socket.socketpair()
        s1.sendall(b"LOG|VM-X|10:00:00|INFO|test-msg\n")
        s1.close()

        receiver._handle_conn(s2)

        assert len(entries) == 1
        assert entries[0].machine_name == "VM-X"
        assert entries[0].content == "test-msg"

    def test_stop_shuts_down_executor(self):
        from master.app.core.log_receiver import LogReceiverThread

        receiver = LogReceiverThread(port=0)
        receiver._running = False  # 不让 run() 真正跑
        receiver.stop()
        assert receiver._executor._shutdown
