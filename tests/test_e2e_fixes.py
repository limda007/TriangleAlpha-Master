"""端到端通信联调测试 — Master ↔ Slave 完整链路"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import socket
from unittest.mock import MagicMock, patch

from common.protocol import (
    TcpCommand,
    build_self_update_payload,
    build_tcp_command,
    build_udp_ext_online,
    build_udp_offline,
    parse_udp_message,
)
from master.app.core.node_manager import NodeManager

# ── UDP 心跳 → NodeManager 状态管理 ──


class TestUdpHeartbeatIntegration:
    """验证 UDP 心跳消息驱动 NodeManager 状态变更"""

    def test_ext_online_registers_node(self):
        nm = NodeManager()
        msg = parse_udp_message(
            build_udp_ext_online("VM-01", "Admin", 50.0, 60.0, "2.0.0", "A组")
        )
        assert msg is not None
        nm.handle_udp_message(msg, "10.0.0.1")

        assert "VM-01" in nm.nodes
        node = nm.nodes["VM-01"]
        assert node.ip == "10.0.0.1"
        assert node.group == "A组"
        assert node.cpu_percent == 50.0

    def test_offline_marks_node_offline(self):
        nm = NodeManager()
        # 先上线
        msg = parse_udp_message(build_udp_ext_online("VM-01", "A", 0, 0, "2", "默认"))
        nm.handle_udp_message(msg, "10.0.0.1")

        # 发送离线
        off_msg = parse_udp_message(build_udp_offline("VM-01"))
        nm.handle_udp_message(off_msg, "10.0.0.1")

        assert nm.nodes["VM-01"].status == "离线"

    def test_heartbeat_signal_chain(self):
        """heart_changed 信号链：上线 → stats_changed（200ms 防抖）"""

        nm = NodeManager()
        stats_calls = []
        nm.stats_changed.connect(lambda: stats_calls.append(True))

        msg = parse_udp_message(build_udp_ext_online("VM-02", "A", 0, 0, "2", "默认"))
        nm.handle_udp_message(msg, "10.0.0.2")

        # stats_changed 有 200ms 防抖，需要处理 QTimer 事件
        # 直接 flush 防抖 timer
        nm._stats_timer.stop()
        nm._flush_stats()

        assert len(stats_calls) >= 1


# ── TCP UPDATETXT → Slave 写入 ──


class TestTcpUpdateTxtE2E:
    """验证 UPDATETXT 端到端：master 构建指令 → slave 解析写入"""

    def test_update_txt_roundtrip(self, tmp_path):
        port = _free_port()
        accounts = "user1----pass1\nuser2----pass2"
        (tmp_path / "accounts.json").write_text('{"stale": true}', encoding="utf-8")
        (tmp_path / "accounts.txt.imported").write_text("old", encoding="utf-8")
        (tmp_path / "runtime_status.json").write_text('{"current_account":"old-user"}', encoding="utf-8")

        async def run():
            from slave.command_handler import CommandHandler

            handler = CommandHandler(str(tmp_path), port=port)
            server_task = asyncio.create_task(handler.run())
            await asyncio.sleep(0.3)

            # 发送 UPDATETXT 指令
            r, w = await asyncio.open_connection("127.0.0.1", port)
            cmd = build_tcp_command(TcpCommand.UPDATE_TXT, payload=accounts)
            w.write((cmd + "\n").encode("utf-8"))
            await w.drain()
            w.close()
            await w.wait_closed()

            await asyncio.sleep(0.3)

            # 验证文件内容
            f = tmp_path / "accounts.txt"
            assert f.exists()
            assert f.read_text(encoding="utf-8") == accounts
            assert not (tmp_path / "accounts.json").exists()
            assert not (tmp_path / "accounts.txt.imported").exists()
            assert not (tmp_path / "runtime_status.json").exists()

            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task

        asyncio.run(run())

    def test_update_self_roundtrip(self, tmp_path):
        port = _free_port()
        new_binary = b"new-slave-binary"
        payload = base64.b64encode(new_binary).decode("ascii")

        async def run():
            from slave.command_handler import CommandHandler

            shutdown_cb = MagicMock()
            with patch("slave.command_handler.launch_self_update_helper"):
                handler = CommandHandler(str(tmp_path), port=port, on_shutdown_requested=shutdown_cb)
                handler.SELF_UPDATE_GRACE_SEC = 0
                server_task = asyncio.create_task(handler.run())
                await asyncio.sleep(0.3)

                _reader, writer = await asyncio.open_connection("127.0.0.1", port)
                cmd = build_tcp_command(
                    TcpCommand.UPDATE_SELF,
                    payload=f"TriangleAlpha-Slave.exe|{payload}",
                )
                with patch("slave.command_handler.os.name", "nt"):
                    writer.write((cmd + "\n").encode("utf-8"))
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                    await asyncio.sleep(0.3)
                    await asyncio.sleep(0)

                pending = tmp_path / "TriangleAlpha-Slave.exe.pending"
                assert pending.exists()
                assert pending.read_bytes() == new_binary
                shutdown_cb.assert_called_once()

                server_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await server_task

        asyncio.run(run())

    def test_update_key_roundtrip(self, tmp_path):
        port = _free_port()
        key = "TESTKEY-ABC123"

        async def run():
            from slave.command_handler import CommandHandler

            handler = CommandHandler(str(tmp_path), port=port)
            server_task = asyncio.create_task(handler.run())
            await asyncio.sleep(0.3)

            r, w = await asyncio.open_connection("127.0.0.1", port)
            cmd = build_tcp_command(TcpCommand.UPDATE_KEY, payload=key)
            w.write((cmd + "\n").encode("utf-8"))
            await w.drain()
            w.close()
            await w.wait_closed()

            await asyncio.sleep(0.3)

            f = tmp_path / "key.txt"
            assert f.exists()
            assert f.read_text(encoding="utf-8") == key

            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task

        asyncio.run(run())


# ── DELETEFILE + 路径遍历防护 ──


class TestDeleteFileE2E:
    """验证 DELETEFILE 正常删除 + 路径遍历被拒绝"""

    def test_delete_normal_file(self, tmp_path):
        port = _free_port()
        target = tmp_path / "old_data.txt"
        target.write_text("delete me")

        async def run():
            from slave.command_handler import CommandHandler

            handler = CommandHandler(str(tmp_path), port=port)
            server_task = asyncio.create_task(handler.run())
            await asyncio.sleep(0.3)

            r, w = await asyncio.open_connection("127.0.0.1", port)
            cmd = build_tcp_command(TcpCommand.DELETE_FILE, payload="old_data.txt")
            w.write((cmd + "\n").encode("utf-8"))
            await w.drain()
            w.close()
            await w.wait_closed()

            await asyncio.sleep(0.3)
            assert not target.exists(), "文件应被删除"

            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task

        asyncio.run(run())

    def test_delete_traversal_rejected(self, tmp_path):
        """路径遍历攻击: ../secret.txt 不应被删除"""
        port = _free_port()
        # 在 tmp_path 的父目录创建文件
        secret = tmp_path.parent / "secret.txt"
        secret.write_text("sensitive data")

        async def run():
            from slave.command_handler import CommandHandler

            handler = CommandHandler(str(tmp_path), port=port)
            server_task = asyncio.create_task(handler.run())
            await asyncio.sleep(0.3)

            r, w = await asyncio.open_connection("127.0.0.1", port)
            cmd = build_tcp_command(TcpCommand.DELETE_FILE, payload="../secret.txt")
            w.write((cmd + "\n").encode("utf-8"))
            await w.drain()
            w.close()
            await w.wait_closed()

            await asyncio.sleep(0.3)
            assert secret.exists(), "路径遍历应被拒绝，文件不应被删除"

            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task

        asyncio.run(run())
        # 清理
        if secret.exists():
            secret.unlink()


# ── 日志消息格式 ──


class TestLogMessageFormat:
    """验证日志消息 LOG| 格式解析"""

    def test_log_receiver_parses_log(self):
        from master.app.core.log_receiver import LogReceiverThread

        receiver = LogReceiverThread(port=0)
        entries = []
        receiver.log_received.connect(entries.append)

        receiver._parse_line("LOG|VM-05|15:22:30|ERROR|连接超时")
        assert len(entries) == 1
        e = entries[0]
        assert e.machine_name == "VM-05"
        assert e.timestamp == "15:22:30"
        assert e.level == "ERROR"
        assert e.content == "连接超时"

    def test_log_with_pipes_in_content(self):
        from master.app.core.log_receiver import LogReceiverThread

        receiver = LogReceiverThread(port=0)
        entries = []
        receiver.log_received.connect(entries.append)

        receiver._parse_line("LOG|VM-01|12:00:00|INFO|key=val|extra|data")
        assert len(entries) == 1
        assert entries[0].content == "key=val|extra|data"


# ── 操作历史联调 ──


class TestHistoryIntegration:
    """验证操作历史信号和记录"""

    def test_history_signal_chain(self):
        nm = NodeManager()
        signals = []
        nm.history_changed.connect(lambda: signals.append(True))

        nm.add_history("下发文件", "5 个节点", "accounts.txt")
        nm.add_history("分发卡密", "3 个节点")

        assert len(signals) == 2
        assert len(nm.history) == 2

    def test_history_dynamic_filter_types(self):
        """验证可以从 history 中提取去重的操作类型"""
        nm = NodeManager()
        nm.add_history("启动脚本", "1")
        nm.add_history("停止脚本", "2")
        nm.add_history("启动脚本", "3")
        nm.add_history("重启电脑", "4")

        op_types = sorted({r.op_type for r in nm.history})
        assert op_types == ["停止脚本", "启动脚本", "重启电脑"]


# ── 协议兼容性 ──


class TestProtocolCompatibility:
    """验证所有 TcpCommand 枚举值在 slave 有对应 handler"""

    def test_all_commands_have_handlers(self):
        """每个 TcpCommand 的 value 都应在 slave _dispatch 中被处理.

        例外: ``UPDATE_TXT_APPEND`` 仅 BETA agent 实现 (P3 doc §4.5.1),
        slave 不会收到也无需 dispatcher, 因此跳过.
        """
        import inspect

        from slave.command_handler import CommandHandler

        dispatch_source = inspect.getsource(CommandHandler._dispatch)
        slave_only = (cmd for cmd in TcpCommand if cmd.name != "UPDATE_TXT_APPEND")
        for cmd in slave_only:
            found = cmd.value in dispatch_source or cmd.name in dispatch_source
            assert found, f"TcpCommand.{cmd.name} ({cmd.value}) 在 _dispatch 中无对应 handler"

    def test_build_and_parse_roundtrip(self):
        """构建 TCP 命令 → 解码验证"""
        # UPDATE_TXT
        cmd = build_tcp_command(TcpCommand.UPDATE_TXT, "hello")
        parts = cmd.split("|", 1)
        assert parts[0] == "UPDATETXT"
        assert base64.b64decode(parts[1]).decode() == "hello"

        # DELETE_FILE
        cmd = build_tcp_command(TcpCommand.DELETE_FILE, "a.txt|b.txt")
        assert cmd == "DELETEFILE|a.txt|b.txt"

        # UPDATE_SELF（payload 原样透传）
        payload = build_self_update_payload("TriangleAlpha-Slave.exe", b"ABC")
        cmd = build_tcp_command(TcpCommand.UPDATE_SELF, payload)
        assert cmd == f"UPDATESELF|{payload}"

        # START_EXE (无 payload)
        cmd = build_tcp_command(TcpCommand.START_EXE)
        assert cmd == "STARTEXE|"


# ── 辅助函数 ──


def _free_port() -> int:
    """获取一个空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
