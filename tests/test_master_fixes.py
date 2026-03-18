"""Master 核心组件联调测试 — 验证所有修复项"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from common.models import AccountInfo, AccountStatus
from common.protocol import TcpCommand, build_tcp_command
from master.app.core.account_pool import AccountPool
from master.app.core.node_manager import NodeManager


# ── C1: themeMode 配置项 ──


class TestC1ThemeMode:
    """验证 config.py 中添加了 themeMode"""

    def test_theme_mode_exists(self):
        from master.app.common.config import cfg
        assert hasattr(cfg, "themeMode"), "cfg 应有 themeMode 属性"

    def test_theme_mode_default_is_auto(self):
        from qfluentwidgets import Theme
        from master.app.common.config import cfg
        assert cfg.get(cfg.themeMode) == Theme.AUTO

    def test_theme_changed_signal_exists(self):
        from master.app.common.config import cfg
        assert hasattr(cfg, "themeChanged"), "QConfig 基类应提供 themeChanged 信号"


# ── C2: 文件操作异常处理 ──


class TestC2FileErrorHandling:
    """验证 AccountPool.load_from_file 对不存在文件抛出 OSError"""

    def test_load_from_nonexistent_file(self, tmp_path):
        pool = AccountPool()
        with pytest.raises(OSError, match="无法读取账号文件"):
            pool.load_from_file(tmp_path / "not_exist.txt")

    def test_load_from_valid_file(self, tmp_path):
        f = tmp_path / "accounts.txt"
        f.write_text("user1----pass1\nuser2----pass2", encoding="utf-8")
        pool = AccountPool()
        pool.load_from_file(f)
        assert pool.total_count == 2


# ── C3: TCP socket 关闭 ──


class TestC3TcpSocketClose:
    """验证 _TcpSendTask 在异常时也关闭 socket"""

    def test_socket_closed_on_connect_failure(self):
        from master.app.core.tcp_commander import _TcpSendTask, TcpCommander

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
        from master.app.core.tcp_commander import _TcpSendTask, TcpCommander

        commander = MagicMock(spec=TcpCommander)
        commander.command_sent = MagicMock()
        task = _TcpSendTask("1.2.3.4", "STARTEXE|", commander)

        mock_sock = MagicMock()
        with patch("master.app.core.tcp_commander.socket.socket", return_value=mock_sock):
            task.run()

        mock_sock.close.assert_called_once()
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


# ── M7: 导出时间戳 ──


class TestM7ExportTimestamp:
    """验证 export_completed 包含完成时间"""

    def test_export_includes_timestamp(self):
        pool = AccountPool()
        pool.load_from_text("user1----pass1\nuser2----pass2")
        # 模拟完成
        pool.accounts[0].status = AccountStatus.COMPLETED
        pool.accounts[0].level = 30
        pool.accounts[0].completed_at = datetime(2026, 3, 18, 14, 30)

        result = pool.export_completed()
        assert "完成时间:" in result
        assert "03-18 14:30" in result
        assert "等级:30" in result

    def test_export_without_completed_at(self):
        pool = AccountPool()
        pool.load_from_text("user1----pass1")
        pool.accounts[0].status = AccountStatus.COMPLETED
        pool.accounts[0].completed_at = None

        result = pool.export_completed()
        assert "完成时间:" in result  # 字段存在，值为空
