"""Slave 核心组件联调测试 — 验证所有修复项"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.protocol import TcpCommand


# ── C5: 路径遍历防护 ──


class TestC5PathTraversal:
    """验证 CommandHandler._safe_path 拒绝路径遍历"""

    def _handler(self, tmp_path):
        from slave.command_handler import CommandHandler
        return CommandHandler(str(tmp_path))

    def test_normal_filename(self, tmp_path):
        h = self._handler(tmp_path)
        result = h._safe_path("accounts.txt")
        assert result is not None
        assert result == (tmp_path / "accounts.txt").resolve()

    def test_parent_traversal_rejected(self, tmp_path):
        h = self._handler(tmp_path)
        assert h._safe_path("../etc/passwd") is None

    def test_nested_traversal_rejected(self, tmp_path):
        h = self._handler(tmp_path)
        assert h._safe_path("sub/../../../etc/passwd") is None

    def test_absolute_path_not_in_base(self, tmp_path):
        h = self._handler(tmp_path)
        assert h._safe_path("/etc/passwd") is None

    def test_subdirectory_allowed(self, tmp_path):
        (tmp_path / "data").mkdir()
        h = self._handler(tmp_path)
        result = h._safe_path("data/file.txt")
        assert result is not None


# ── C6: 进程句柄管理 ──


class TestC6ProcessHandle:
    """验证 ProcessManager 保存和清理进程句柄"""

    def test_initial_process_is_none(self, tmp_path):
        from slave.process_manager import ProcessManager
        pm = ProcessManager(str(tmp_path))
        assert pm._process is None

    def test_start_saves_process_handle(self, tmp_path):
        from slave.process_manager import ProcessManager

        pm = ProcessManager(str(tmp_path))
        (tmp_path / "TestDemo.exe").write_text("fake")
        mock_proc = MagicMock()

        async def run():
            with patch.object(pm, "kill_by_name", new_callable=AsyncMock, return_value=0):
                with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
                    result = await pm.start_testdemo()
                    assert result is True
                    assert pm._process is mock_proc

        asyncio.run(run())

    def test_stop_all_terminates_saved_process(self, tmp_path):
        from slave.process_manager import ProcessManager

        pm = ProcessManager(str(tmp_path))
        mock_proc = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        pm._process = mock_proc

        async def run():
            with patch.object(pm, "kill_by_name", new_callable=AsyncMock, return_value=0):
                await pm.stop_all()
                mock_proc.terminate.assert_called_once()
                assert pm._process is None

        asyncio.run(run())


# ── C7: 心跳异常日志 ──


class TestC7HeartbeatErrorHandling:
    """验证 HeartbeatService.run 包含异常处理和重试逻辑"""

    def test_run_has_error_counter(self):
        from slave.heartbeat import HeartbeatService
        source = inspect.getsource(HeartbeatService.run)
        assert "consecutive_errors" in source
        assert "consecutive_errors += 1" in source

    def test_run_resets_counter_on_success(self):
        from slave.heartbeat import HeartbeatService
        source = inspect.getsource(HeartbeatService.run)
        assert "consecutive_errors = 0" in source

    def test_run_has_threshold_check(self):
        from slave.heartbeat import HeartbeatService
        source = inspect.getsource(HeartbeatService.run)
        assert "consecutive_errors >= 10" in source


# ── H5: 端口绑定重试 ──


class TestH5PortBindRetry:
    """验证 CommandHandler.run() 端口绑定重试"""

    def test_run_catches_bind_error(self):
        from slave.command_handler import CommandHandler
        source = inspect.getsource(CommandHandler.run)
        assert "OSError" in source, "run() 应捕获 OSError"

    def test_run_retries_after_failure(self):
        from slave.command_handler import CommandHandler
        source = inspect.getsource(CommandHandler.run)
        assert "max_retries" in source, "run() 应有 max_retries 重试上限"
        assert "for attempt" in source or "range(max_retries)" in source, "run() 应有循环重试"
        assert "reuse_address" in source, "run() 应使用 reuse_address"


# ── H6: 日志上报重试 ──


class TestH6LogReporterRetry:
    """验证 LogReporter 失败重试机制"""

    def test_has_retry_counter(self):
        from slave.log_reporter import LogReporter
        source = inspect.getsource(LogReporter.run)
        assert "retries" in source
        assert "max_retries" in source

    def test_exponential_backoff(self):
        from slave.log_reporter import LogReporter
        source = inspect.getsource(LogReporter.run)
        assert "2 ** retries" in source or "2**retries" in source

    def test_reinserts_to_queue_on_failure(self):
        from slave.log_reporter import LogReporter
        source = inspect.getsource(LogReporter.run)
        assert "put_nowait" in source, "重试耗尽后应将日志推回队列"


# ── H7: EXT_QUERY 移除 ──


class TestH7SlaveExtQuery:
    def test_no_ext_query_in_tcpcommand(self):
        assert not hasattr(TcpCommand, "EXT_QUERY")

    def test_remaining_commands(self):
        expected = {"UPDATE_TXT", "START_EXE", "STOP_EXE", "REBOOT_PC",
                    "UPDATE_KEY", "DELETE_FILE", "EXT_SET_GROUP"}
        assert {m.name for m in TcpCommand} == expected


# ── H9: 精确进程匹配 ──


class TestH9ExactProcessMatch:
    """验证远控查杀使用精确匹配"""

    def test_exact_match_in_source(self):
        from slave.auto_setup import kill_remote_controls
        source = inspect.getsource(kill_remote_controls)
        assert '== name.lower() + ".exe"' in source, "应使用 == 精确匹配"
        # 进程匹配不应再使用 startswith（注释行解析的 startswith 不算）
        assert 'pname.lower().startswith' not in source, "进程匹配不应使用 startswith"

    def test_exact_match_behavior(self, tmp_path):
        """ToDesk.exe 匹配，ToDeskService.exe 不匹配"""
        list_file = tmp_path / "关闭远控列表.txt"
        list_file.write_text("ToDesk\n", encoding="utf-8")

        procs = [
            MagicMock(info={"name": "ToDesk.exe"}),
            MagicMock(info={"name": "ToDeskService.exe"}),
            MagicMock(info={"name": "ToDeskUpdate.exe"}),
        ]
        for p in procs:
            p.kill = MagicMock()

        async def run():
            with patch("slave.auto_setup.asyncio.sleep", new_callable=AsyncMock):
                with patch("slave.auto_setup.psutil.process_iter", return_value=procs):
                    from slave.auto_setup import kill_remote_controls
                    await kill_remote_controls(tmp_path)

        asyncio.run(run())

        procs[0].kill.assert_called_once()      # ToDesk.exe — 应匹配
        procs[1].kill.assert_not_called()        # ToDeskService.exe — 不应匹配
        procs[2].kill.assert_not_called()        # ToDeskUpdate.exe — 不应匹配


# ── H10: 命令注入防护 ──


class TestH10CommandInjection:
    """验证 check_rename 使用 subprocess.run 而非 os.system"""

    def test_uses_subprocess_run(self):
        from slave.auto_setup import check_rename
        source = inspect.getsource(check_rename)
        assert "subprocess.run" in source, "应使用 subprocess.run"
        assert "os.system" not in source, "不应使用 os.system"
        assert "shell=False" in source


# ── M4: 文件大小限制 ──


class TestM4FileSizeLimit:
    """验证 CommandHandler 文件大小限制"""

    def test_max_file_size(self):
        from slave.command_handler import CommandHandler
        assert CommandHandler.MAX_FILE_SIZE == 100 * 1024 * 1024

    def test_max_file_size_in_dispatch(self):
        from slave.command_handler import CommandHandler
        source = inspect.getsource(CommandHandler._dispatch)
        assert "MAX_FILE_SIZE" in source, "_dispatch 应检查 MAX_FILE_SIZE"
