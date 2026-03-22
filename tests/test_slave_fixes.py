"""Slave 核心组件联调测试 — 验证所有修复项"""
from __future__ import annotations

import asyncio
import inspect
import os
from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_start_launches_independently(self, tmp_path):
        """start_launcher 用 cmd /c start 独立启动，不保存 process handle"""
        from slave.process_manager import ProcessManager

        pm = ProcessManager(str(tmp_path))
        (tmp_path / "TriangleAlpha.Launcher.exe").write_text("fake")

        async def run():
            with (
                patch.object(pm, "kill_by_name", new_callable=AsyncMock, return_value=0),
                patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
            ):
                result = await pm.start_launcher()
                assert result is True
                mock_exec.assert_called_once()
                # cmd /c start 模式不保存 _process
                assert pm._process is None

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
    """验证 LogReporter 失败重试机制（重试逻辑在 _send_lines 中）"""

    def test_has_retry_loop(self):
        from slave.log_reporter import LogReporter
        source = inspect.getsource(LogReporter._send_lines)
        assert "for attempt in range" in source, "_send_lines 应有重试循环"

    def test_exponential_backoff(self):
        from slave.log_reporter import LogReporter
        source = inspect.getsource(LogReporter._send_lines)
        assert "2 ** (attempt" in source or "2**(attempt" in source, "_send_lines 应有指数退避"

    def test_persistent_connection(self):
        from slave.log_reporter import LogReporter
        source = inspect.getsource(LogReporter._ensure_connection)
        assert "self._writer" in source, "应维护持久 TCP 连接"


# ── H7: EXT_QUERY 移除 ──


class TestH7SlaveExtQuery:
    def test_no_ext_query_in_tcpcommand(self):
        assert not hasattr(TcpCommand, "EXT_QUERY")

    def test_remaining_commands(self):
        expected = {"UPDATE_TXT", "START_EXE", "STOP_EXE", "REBOOT_PC",
                    "UPDATE_KEY", "DELETE_FILE", "EXT_SET_GROUP", "EXT_SET_CONFIG"}
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
            with (
                patch("slave.auto_setup.asyncio.sleep", new_callable=AsyncMock),
                patch("slave.auto_setup.psutil.process_iter", return_value=procs),
            ):
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
    """验证 CommandHandler 使用 STREAM_LIMIT"""

    def test_stream_limit(self):
        from slave.command_handler import CommandHandler
        assert CommandHandler.STREAM_LIMIT == 1024 * 1024


# ── H11: Slave 单实例保护 ──


class TestH11SlaveSingleInstance:
    """验证 slave 入口使用原子锁文件实现单实例保护"""

    def test_acquire_instance_lock_creates_lock_file(self, tmp_path):
        from slave.main import acquire_instance_lock

        lock = acquire_instance_lock(tmp_path / ".slave.pid")

        assert lock is not None
        assert (tmp_path / ".slave.pid").read_text(encoding="utf-8").strip() == str(os.getpid())
        lock.release()
        assert not (tmp_path / ".slave.pid").exists()

    def test_acquire_instance_lock_rejects_active_process(self, tmp_path):
        from slave.main import acquire_instance_lock

        pid_path = tmp_path / ".slave.pid"
        pid_path.write_text("12345", encoding="utf-8")

        with patch("slave.main.psutil.pid_exists", return_value=True):
            lock = acquire_instance_lock(pid_path)

        assert lock is None
        assert pid_path.exists()

    def test_acquire_instance_lock_reclaims_stale_lock(self, tmp_path):
        from slave.main import acquire_instance_lock

        pid_path = tmp_path / ".slave.pid"
        pid_path.write_text("12345", encoding="utf-8")

        with patch("slave.main.psutil.pid_exists", return_value=False):
            lock = acquire_instance_lock(pid_path)

        assert lock is not None
        assert pid_path.read_text(encoding="utf-8").strip() == str(os.getpid())
        lock.release()

    def test_release_does_not_delete_replaced_lock_file(self, tmp_path):
        from slave.main import acquire_instance_lock

        pid_path = tmp_path / ".slave.pid"
        lock = acquire_instance_lock(pid_path)

        assert lock is not None
        pid_path.unlink()
        pid_path.write_text("99999", encoding="utf-8")

        lock.release()

        assert pid_path.exists()
        assert pid_path.read_text(encoding="utf-8").strip() == "99999"

    def test_main_warns_and_exits_when_instance_lock_exists(self, tmp_path):
        import pytest

        from slave.main import main

        mock_app = MagicMock()

        with (
            patch("slave.main.QApplication", return_value=mock_app) as mock_qapp,
            patch("slave.main._get_base_dir", return_value=tmp_path),
            patch("slave.main.acquire_instance_lock", return_value=None) as mock_lock,
            patch("slave.main.QMessageBox.warning") as mock_warning,
            patch("slave.main.SlaveWindow") as mock_window,
            patch("slave.main.SlaveBackend") as mock_backend,
            patch("slave.main.sys.exit", side_effect=SystemExit) as mock_exit,
            pytest.raises(SystemExit),
        ):
            main()

        mock_qapp.assert_called_once()
        mock_app.setQuitOnLastWindowClosed.assert_called_once_with(False)
        mock_lock.assert_called_once()
        # 锁文件路径在系统临时目录
        actual_path = mock_lock.call_args[0][0]
        assert actual_path.name == "TriangleAlphaSlave.pid"
        mock_warning.assert_called_once_with(
            None,
            "TA-Slave",
            "已有实例在运行中，请勿重复启动。",
        )
        mock_exit.assert_called_once_with(0)
        mock_window.assert_not_called()
        mock_backend.assert_not_called()
        mock_app.exec.assert_not_called()

    def test_main_warns_when_backend_does_not_stop(self, tmp_path, capsys):
        """backend.wait 超时时应打印警告而非 raise"""
        import pytest

        from slave.main import main

        mock_app = MagicMock()
        mock_app.exec.return_value = 0
        mock_backend = MagicMock()
        mock_backend.wait.return_value = False
        mock_lock = MagicMock()

        with (
            patch("slave.main.QApplication", return_value=mock_app),
            patch("slave.main._get_base_dir", return_value=tmp_path),
            patch("slave.main.acquire_instance_lock", return_value=mock_lock),
            patch("slave.main._read_master_ip", return_value=None),
            patch("slave.main.SlaveWindow", return_value=MagicMock()),
            patch("slave.main.SlaveBackend", return_value=mock_backend),
            pytest.raises(SystemExit),
        ):
            main()

        mock_backend.stop.assert_called_once()
        mock_backend.wait.assert_called_once_with(5000)
        # 超时后仍应释放锁
        mock_lock.release.assert_called_once()
        captured = capsys.readouterr()
        assert "SlaveBackend" in captured.out
