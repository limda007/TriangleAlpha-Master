"""Slave 模块单元测试：heartbeat、process_manager"""

import asyncio
import platform
from unittest.mock import AsyncMock, MagicMock

from common.protocol import UdpMessageType, build_udp_ext_online, parse_udp_message
from slave.heartbeat import HeartbeatService
from slave.process_manager import ProcessManager


class TestHeartbeatService:
    def test_default_machine_name(self):
        svc = HeartbeatService()
        assert svc.machine_name == platform.node()

    def test_set_group(self):
        svc = HeartbeatService()
        svc.set_group("A组")
        assert svc._group == "A组"

    def test_builds_valid_ext_online_message(self):
        msg_str = build_udp_ext_online("VM-01", "Admin", 45.2, 60.1, "2.0.0", "A组")
        parsed = parse_udp_message(msg_str)
        assert parsed is not None
        assert parsed.type == UdpMessageType.EXT_ONLINE
        assert parsed.machine_name == "VM-01"
        assert parsed.cpu_percent == 45.2
        assert parsed.group == "A组"

    def test_custom_master_ip_and_port(self):
        svc = HeartbeatService(master_ip="192.168.1.100", port=9999, interval=5)
        assert svc._master_ip == "192.168.1.100"
        assert svc._port == 9999
        assert svc._interval == 5

    def test_stop_sets_running_false(self):
        svc = HeartbeatService()
        svc._running = True
        svc.stop()
        assert svc._running is False


class TestProcessManager:
    def test_init_with_base_dir(self, tmp_path):
        pm = ProcessManager(str(tmp_path))
        assert pm._base_dir == tmp_path


class TestCommandHandlerShutdown:
    def test_stop_closes_server(self, tmp_path):
        from slave.command_handler import CommandHandler

        handler = CommandHandler(str(tmp_path))
        server = MagicMock()
        server.wait_closed = AsyncMock(return_value=None)
        handler._server = server

        async def run():
            await handler.stop()

        asyncio.run(run())

        server.close.assert_called_once()
        server.wait_closed.assert_awaited_once()
        assert handler._server is None


class TestLogReporterShutdown:
    def test_stop_waits_for_executor_to_flush(self):
        from slave.log_reporter import LogReporter

        reporter = LogReporter("127.0.0.1", "VM-01")
        reporter._executor = MagicMock()

        async def run():
            await reporter.stop()

        asyncio.run(run())

        reporter._executor.shutdown.assert_called_once_with(wait=True)

    def test_stop_does_not_block_on_executor_thread(self):
        """验证 stop() 不会因 executor 线程阻塞在 queue.get() 而长时间等待"""
        import time

        from slave.log_reporter import LogReporter

        reporter = LogReporter("127.0.0.1", "VM-01")
        # 模拟 run() 中的阻塞 get：executor 线程在 queue.get(timeout=5) 上等待
        reporter._executor.submit(reporter._queue.get, timeout=5)
        time.sleep(0.1)  # 确保线程已开始阻塞

        start = time.time()
        asyncio.run(reporter.stop())
        elapsed = time.time() - start

        assert elapsed < 2.0, f"stop() 耗时 {elapsed:.1f}s，超过 2s 阈值"
