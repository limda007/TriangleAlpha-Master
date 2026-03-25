"""Slave 核心组件联调测试 — 验证所有修复项"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.protocol import ParsedTcpCommand, TcpCommand

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


class TestC5SetConfigPathGuard:
    """验证配置下发移除白名单后，仅保留路径安全限制。"""

    def _handler(self, tmp_path):
        from slave.command_handler import CommandHandler

        return CommandHandler(str(tmp_path))

    def test_allows_binary_in_base_dir(self, tmp_path):
        handler = self._handler(tmp_path)
        payload = "SlaveClientConsole.exe|BASE64:" + base64.b64encode(b"fake-exe").decode("ascii")

        desc = handler._handle_set_config(ParsedTcpCommand(TcpCommand.EXT_SET_CONFIG, payload))

        target = tmp_path / "SlaveClientConsole.exe"
        assert desc == "文件已更新: SlaveClientConsole.exe"
        assert target.read_bytes() == b"fake-exe"

    def test_allows_arbitrary_relative_file(self, tmp_path):
        handler = self._handler(tmp_path)
        (tmp_path / "configs").mkdir()
        payload = "configs/custom.bin|BASE64:" + base64.b64encode(b"hello").decode("ascii")

        desc = handler._handle_set_config(ParsedTcpCommand(TcpCommand.EXT_SET_CONFIG, payload))

        target = tmp_path / "configs" / "custom.bin"
        assert desc == "文件已更新: configs/custom.bin"
        assert target.read_bytes() == b"hello"

    def test_binary_update_replaces_existing_file_instead_of_in_place_write(self, tmp_path):
        handler = self._handler(tmp_path)
        target = tmp_path / "TestDemo.exe"
        target.write_bytes(b"old")
        payload = "TestDemo.exe|BASE64:" + base64.b64encode(b"new-binary").decode("ascii")

        with patch("slave.command_handler.os.replace") as mock_replace:
            desc = handler._handle_set_config(ParsedTcpCommand(TcpCommand.EXT_SET_CONFIG, payload))

        assert desc == "文件已更新: TestDemo.exe"
        mock_replace.assert_called_once()
        src_arg, dst_arg = mock_replace.call_args.args
        assert Path(dst_arg) == target
        assert Path(src_arg).parent == tmp_path

    def test_binary_update_requests_zone_identifier_cleanup(self, tmp_path):
        handler = self._handler(tmp_path)
        target = tmp_path / "TestDemo.exe"
        payload = "TestDemo.exe|BASE64:" + base64.b64encode(b"new-binary").decode("ascii")

        with patch("slave.command_handler.clear_zone_identifier") as mock_clear:
            desc = handler._handle_set_config(ParsedTcpCommand(TcpCommand.EXT_SET_CONFIG, payload))

        assert desc == "文件已更新: TestDemo.exe"
        assert target.read_bytes() == b"new-binary"
        mock_clear.assert_called_once_with(target)

    def test_rejects_traversal_even_without_whitelist(self, tmp_path):
        handler = self._handler(tmp_path)
        payload = "../OtherConsole.exe|BASE64:" + base64.b64encode(b"fake-exe").decode("ascii")

        desc = handler._handle_set_config(ParsedTcpCommand(TcpCommand.EXT_SET_CONFIG, payload))

        assert desc == ""
        assert not (tmp_path.parent / "OtherConsole.exe").exists()


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
        expected = {
            "UPDATE_TXT", "UPDATE_SELF", "START_EXE", "STOP_EXE", "REBOOT_PC",
            "UPDATE_KEY", "DELETE_FILE", "EXT_SET_GROUP", "EXT_SET_CONFIG", "PUSH_KAMI",
        }
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


class TestH12StartupSetup:
    """验证启动目录和启动命令不再依赖写死路径。"""

    def test_resolve_startup_dir_prefers_special_folder(self, tmp_path):
        from slave.auto_setup import _resolve_startup_dir

        result = MagicMock(returncode=0, stdout=f"{tmp_path}\n", stderr="")
        with patch("slave.auto_setup._run_cscript", return_value=result):
            startup_dir = _resolve_startup_dir()

        assert startup_dir == tmp_path

    def test_build_start_command_sets_workdir(self, tmp_path):
        from slave.auto_setup import _build_start_command

        exe_path = tmp_path / "nested" / "TriangleAlpha-Slave.exe"
        command = _build_start_command(exe_path)

        assert 'start "" /d "' in command
        assert str(exe_path.parent) in command
        assert str(exe_path) in command


# ── M4: 文件大小限制 ──


class TestM4FileSizeLimit:
    """验证 CommandHandler 使用 STREAM_LIMIT"""

    def test_stream_limit(self):
        from slave.command_handler import CommandHandler
        assert CommandHandler.STREAM_LIMIT == 64 * 1024 * 1024


class TestM5SelfUpdate:
    """验证 slave 自更新的暂存与 helper 生成。"""

    def test_prepare_self_update_writes_pending_and_helper(self, tmp_path):
        from common.protocol import build_self_update_payload
        from slave.self_update import prepare_self_update

        exe_path = tmp_path / "nested" / "TriangleAlpha-Slave.exe"
        exe_path.parent.mkdir(parents=True)
        exe_path.write_bytes(b"old")
        guard_lock_path = tmp_path / "TriangleAlphaSlave.guard.pid"
        guard_lock_path.write_text("1234", encoding="utf-8")
        payload = build_self_update_payload("TriangleAlpha-Slave.exe", b"new-binary")

        with patch("slave.self_update.tempfile.gettempdir", return_value=str(tmp_path)):
            update = prepare_self_update(
                tmp_path,
                payload,
                current_pid=4321,
                current_executable=exe_path,
            )

        assert update.pending_path.read_bytes() == b"new-binary"
        assert update.guardian_pid == 1234
        assert update.guard_lock_path == guard_lock_path
        helper_text = update.helper_path.read_text(encoding="utf-8")
        assert str(exe_path) in helper_text
        assert str(update.pending_path) in helper_text
        assert "4321" in helper_text
        assert "GUARD_PID=1234" in helper_text
        assert "if errorlevel 1 goto wait_guard_pid" in helper_text
        assert str(guard_lock_path) in helper_text
        assert "PYINSTALLER_RESET_ENVIRONMENT" in helper_text

    def test_prepare_self_update_requests_zone_identifier_cleanup(self, tmp_path):
        from common.protocol import build_self_update_payload
        from slave.self_update import prepare_self_update

        exe_path = tmp_path / "TriangleAlpha-Slave.exe"
        exe_path.write_bytes(b"old")
        payload = build_self_update_payload("TriangleAlpha-Slave.exe", b"new-binary")

        with (
            patch("slave.self_update.tempfile.gettempdir", return_value=str(tmp_path)),
            patch("slave.self_update.clear_zone_identifier") as mock_clear,
        ):
            update = prepare_self_update(
                tmp_path,
                payload,
                current_pid=4321,
                current_executable=exe_path,
            )

        mock_clear.assert_called_once_with(update.pending_path)

    def test_prepare_self_update_ignores_same_guard_pid(self, tmp_path):
        from common.protocol import build_self_update_payload
        from slave.self_update import prepare_self_update

        exe_path = tmp_path / "TriangleAlpha-Slave.exe"
        exe_path.write_bytes(b"old")
        (tmp_path / "TriangleAlphaSlave.guard.pid").write_text("4321", encoding="utf-8")
        payload = build_self_update_payload("TriangleAlpha-Slave.exe", b"new-binary")

        with patch("slave.self_update.tempfile.gettempdir", return_value=str(tmp_path)):
            update = prepare_self_update(
                tmp_path,
                payload,
                current_pid=4321,
                current_executable=exe_path,
            )

        assert update.guardian_pid is None

    def test_prepare_self_update_rejects_sha256_mismatch(self, tmp_path):
        from common.protocol import build_self_update_payload
        from slave.self_update import prepare_self_update

        exe_path = tmp_path / "TriangleAlpha-Slave.exe"
        exe_path.write_bytes(b"old")
        payload = build_self_update_payload("TriangleAlpha-Slave.exe", b"new-binary")
        wrong_sha256 = hashlib.sha256(b"other-binary").hexdigest()
        payload = payload.replace(
            hashlib.sha256(b"new-binary").hexdigest(),
            wrong_sha256,
            1,
        )

        with pytest.raises(ValueError, match="SHA256"):
            prepare_self_update(
                tmp_path,
                payload,
                current_pid=4321,
                current_executable=exe_path,
            )

    def test_prepare_self_update_rejects_size_mismatch(self, tmp_path):
        from common.protocol import build_self_update_payload
        from slave.self_update import prepare_self_update

        exe_path = tmp_path / "TriangleAlpha-Slave.exe"
        exe_path.write_bytes(b"old")
        payload = build_self_update_payload("TriangleAlpha-Slave.exe", b"new-binary").replace(
            "SIZE:10",
            "SIZE:999",
            1,
        )

        with pytest.raises(ValueError, match="大小不匹配"):
            prepare_self_update(
                tmp_path,
                payload,
                current_pid=4321,
                current_executable=exe_path,
            )

    def test_launch_self_update_helper_resets_pyinstaller_env(self, tmp_path):
        from slave.self_update import PreparedSelfUpdate, launch_self_update_helper

        update = PreparedSelfUpdate(
            filename="TriangleAlpha-Slave.exe",
            target_path=tmp_path / "TriangleAlpha-Slave.exe",
            pending_path=tmp_path / "TriangleAlpha-Slave.exe.pending",
            helper_path=tmp_path / "TriangleAlpha-Slave.exe.update.cmd",
        )

        with (
            patch("slave.self_update.os.name", "nt"),
            patch.dict(
                "slave.self_update.os.environ",
                {"_PYI_APPLICATION_HOME_DIR": "C:/Temp/_MEI123", "PATH": "C:/Windows/System32"},
                clear=False,
            ),
            patch("slave.self_update.subprocess.Popen") as mock_popen,
        ):
            launch_self_update_helper(update)

        env = mock_popen.call_args.kwargs["env"]
        assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
        assert "_PYI_APPLICATION_HOME_DIR" not in env

    def test_handle_update_self_requests_shutdown(self, tmp_path):
        from common.protocol import ParsedTcpCommand
        from slave.command_handler import CommandHandler

        shutdown_cb = MagicMock()
        handler = CommandHandler(str(tmp_path), on_shutdown_requested=shutdown_cb)
        parsed = ParsedTcpCommand(TcpCommand.UPDATE_SELF, "TriangleAlpha-Slave.exe|QUJD")
        prepared = MagicMock(filename="TriangleAlpha-Slave.exe")

        async def run():
            with (
                patch("slave.command_handler.os.name", "nt"),
                patch("slave.command_handler.prepare_self_update", return_value=prepared),
                patch("slave.command_handler.launch_self_update_helper"),
            ):
                handler.SELF_UPDATE_GRACE_SEC = 0
                desc = await handler._handle_update_self(parsed)
                await asyncio.sleep(0)

            assert desc == "Slave 自更新已接收: TriangleAlpha-Slave.exe"

        asyncio.run(run())
        shutdown_cb.assert_called_once()


class TestM5WindowsSecurity:
    """验证 Windows 文件安全标记清理辅助逻辑。"""

    def test_clear_zone_identifier_ignores_non_windows(self, tmp_path):
        from slave.windows_security import clear_zone_identifier

        with (
            patch("slave.windows_security.os.name", "posix"),
            patch("slave.windows_security.os.remove") as mock_remove,
        ):
            result = clear_zone_identifier(tmp_path / "TestDemo.exe")

        assert result is False
        mock_remove.assert_not_called()

    def test_clear_zone_identifier_removes_ads_on_windows(self, tmp_path):
        from slave.windows_security import clear_zone_identifier, zone_identifier_path

        target = tmp_path / "TestDemo.exe"

        with (
            patch("slave.windows_security.os.name", "nt"),
            patch("slave.windows_security.os.remove") as mock_remove,
        ):
            result = clear_zone_identifier(target)

        assert result is True
        mock_remove.assert_called_once_with(zone_identifier_path(target))


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

    def test_run_slave_app_silently_retries_when_child_hits_instance_lock(self, tmp_path):
        from slave.main import _GUARD_CHILD_BUSY_EXIT_CODE, _run_slave_app

        mock_app = MagicMock()

        with (
            patch("slave.main.configure_slave_logging"),
            patch("slave.main._ensure_console_placeholder"),
            patch("slave.main.QApplication", return_value=mock_app),
            patch("slave.main._get_base_dir", return_value=tmp_path),
            patch("slave.main.acquire_instance_lock", return_value=None),
            patch("slave.main._is_guard_child_mode", return_value=True),
            patch("slave.main.QMessageBox.warning") as mock_warning,
            patch("slave.main._notify_guard_stop") as mock_notify,
        ):
            result = _run_slave_app()

        assert result == _GUARD_CHILD_BUSY_EXIT_CODE
        mock_warning.assert_not_called()
        mock_notify.assert_not_called()
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

    def test_ensure_console_placeholder_prefers_small_stub(self, tmp_path):
        from slave.main import SLAVE_CLIENT_CONSOLE_FILENAME, _ensure_console_placeholder

        exe_path = tmp_path / "TriangleAlpha-Slave.exe"
        exe_path.write_bytes(b"fake-exe")
        placeholder_path = tmp_path / SLAVE_CLIENT_CONSOLE_FILENAME

        def _fake_build(path):
            path.write_bytes(b"MZstub")
            return True

        with (
            patch("slave.main.os.name", "nt"),
            patch("slave.main.sys.frozen", True, create=True),
            patch("slave.main._build_console_placeholder_stub", side_effect=_fake_build),
        ):
            placeholder = _ensure_console_placeholder(exe_path)

        assert placeholder == placeholder_path
        assert placeholder is not None
        assert placeholder.read_bytes() == b"MZstub"

    def test_ensure_console_placeholder_copies_current_executable_when_stub_build_fails(self, tmp_path):
        from slave.main import SLAVE_CLIENT_CONSOLE_FILENAME, _ensure_console_placeholder

        exe_path = tmp_path / "TriangleAlpha-Slave.exe"
        exe_path.write_bytes(b"fake-exe")

        with (
            patch("slave.main.os.name", "nt"),
            patch("slave.main.sys.frozen", True, create=True),
            patch("slave.main._build_console_placeholder_stub", return_value=False),
        ):
            placeholder = _ensure_console_placeholder(exe_path)

        assert placeholder == tmp_path / SLAVE_CLIENT_CONSOLE_FILENAME
        assert placeholder is not None
        assert placeholder.read_bytes() == b"fake-exe"

    def test_ensure_console_placeholder_keeps_existing_fresh_small_stub(self, tmp_path):
        from slave.main import SLAVE_CLIENT_CONSOLE_FILENAME, _ensure_console_placeholder

        exe_path = tmp_path / "TriangleAlpha-Slave.exe"
        exe_path.write_bytes(b"x" * (2 * 1024 * 1024))
        placeholder_path = tmp_path / SLAVE_CLIENT_CONSOLE_FILENAME
        placeholder_path.write_bytes(b"MZtiny")
        placeholder_path.touch()
        os.utime(placeholder_path, (exe_path.stat().st_mtime + 10, exe_path.stat().st_mtime + 10))

        with (
            patch("slave.main.os.name", "nt"),
            patch("slave.main.sys.frozen", True, create=True),
            patch("slave.main._build_console_placeholder_stub") as mock_build,
        ):
            placeholder = _ensure_console_placeholder(exe_path)

        assert placeholder == placeholder_path
        assert placeholder is not None
        assert placeholder.read_bytes() == b"MZtiny"
        mock_build.assert_not_called()

    def test_ensure_console_placeholder_rebuilds_stale_small_stub(self, tmp_path):
        from slave.main import SLAVE_CLIENT_CONSOLE_FILENAME, _ensure_console_placeholder

        exe_path = tmp_path / "TriangleAlpha-Slave.exe"
        exe_path.write_bytes(b"x" * (2 * 1024 * 1024))
        placeholder_path = tmp_path / SLAVE_CLIENT_CONSOLE_FILENAME
        placeholder_path.write_bytes(b"MZold")
        os.utime(placeholder_path, (exe_path.stat().st_mtime - 10, exe_path.stat().st_mtime - 10))

        def _fake_build(path):
            path.write_bytes(b"MZnew")
            return True

        with (
            patch("slave.main.os.name", "nt"),
            patch("slave.main.sys.frozen", True, create=True),
            patch("slave.main._build_console_placeholder_stub", side_effect=_fake_build) as mock_build,
        ):
            placeholder = _ensure_console_placeholder(exe_path)

        assert placeholder == placeholder_path
        assert placeholder is not None
        assert placeholder.read_bytes() == b"MZnew"
        mock_build.assert_called_once_with(placeholder_path)

    def test_run_console_placeholder_waits_until_parent_exits(self):
        from slave.main import _run_console_placeholder

        with (
            patch("slave.main.psutil.pid_exists", side_effect=[True, False]) as mock_exists,
            patch("slave.main.time.sleep") as mock_sleep,
        ):
            result = _run_console_placeholder(parent_pid=4321, poll_interval_sec=0.1, max_wait_sec=1)

        assert result == 0
        assert mock_exists.call_count == 2
        mock_sleep.assert_called_once_with(0.1)

    def test_main_exits_in_console_placeholder_mode(self):
        import pytest

        from slave.main import main

        with (
            patch("slave.main._is_console_placeholder_mode", return_value=True),
            patch("slave.main._run_console_placeholder", return_value=0) as mock_placeholder,
            patch("slave.main.configure_slave_logging") as mock_logging,
            patch("slave.main.QApplication") as mock_qapp,
            patch("slave.main.sys.exit", side_effect=SystemExit) as mock_exit,
            pytest.raises(SystemExit),
        ):
            main()

        mock_placeholder.assert_called_once()
        mock_logging.assert_not_called()
        mock_qapp.assert_not_called()
        mock_exit.assert_called_once_with(0)

    def test_main_uses_guardian_in_packaged_windows_mode(self):
        import pytest

        from slave.main import main

        with (
            patch("slave.main.os.name", "nt"),
            patch("slave.main.sys.frozen", True, create=True),
            patch("slave.main._is_console_placeholder_mode", return_value=False),
            patch("slave.main._is_guard_process_mode", return_value=False),
            patch("slave.main._is_guard_child_mode", return_value=False),
            patch("slave.main._current_executable_path", return_value=Path("C:/TA/TriangleAlpha-Slave.exe")),
            patch("slave.main._run_guardian", return_value=0) as mock_guardian,
            patch("slave.main._run_slave_app") as mock_child,
            patch("slave.main.sys.exit", side_effect=SystemExit) as mock_exit,
            pytest.raises(SystemExit),
        ):
            main()

        mock_guardian.assert_called_once()
        mock_child.assert_not_called()
        mock_exit.assert_called_once_with(0)

    def test_run_guardian_restarts_child_after_crash(self):
        from slave.main import _run_guardian

        child1 = MagicMock()
        child1.wait.return_value = 1
        child2 = MagicMock()
        child2.wait.return_value = 0
        mock_lock = MagicMock()

        with (
            patch("slave.main._should_use_guardian", return_value=True),
            patch("slave.main.acquire_instance_lock", return_value=mock_lock),
            patch("slave.main._clear_guard_action"),
            patch("slave.main._spawn_guarded_child", side_effect=[child1, child2]) as mock_spawn,
            patch("slave.main._consume_guard_action", side_effect=["", "stop"]),
            patch("slave.main.time.monotonic", side_effect=[100.0, 102.0, 200.0, 265.0]),
            patch("slave.main.time.sleep") as mock_sleep,
        ):
            result = _run_guardian(Path("C:/TA/TriangleAlpha-Slave.exe"))

        assert result == 0
        assert mock_spawn.call_count == 2
        mock_sleep.assert_called_once_with(3.0)
        mock_lock.release.assert_called_once()

    def test_run_guardian_returns_when_guard_lock_exists(self):
        from slave.main import _run_guardian

        with (
            patch("slave.main._should_use_guardian", return_value=True),
            patch("slave.main.acquire_instance_lock", return_value=None),
            patch("slave.main._spawn_guarded_child") as mock_spawn,
        ):
            result = _run_guardian(Path("C:/TA/TriangleAlpha-Slave.exe"))

        assert result == 0
        mock_spawn.assert_not_called()

    def test_run_guardian_quick_retries_when_child_reports_instance_busy(self):
        from slave.main import _GUARD_CHILD_BUSY_EXIT_CODE, _run_guardian

        child1 = MagicMock()
        child1.wait.return_value = _GUARD_CHILD_BUSY_EXIT_CODE
        child2 = MagicMock()
        child2.wait.return_value = 0
        mock_lock = MagicMock()

        with (
            patch("slave.main._should_use_guardian", return_value=True),
            patch("slave.main.acquire_instance_lock", return_value=mock_lock),
            patch("slave.main._clear_guard_action"),
            patch("slave.main._spawn_guarded_child", side_effect=[child1, child2]) as mock_spawn,
            patch("slave.main._consume_guard_action", side_effect=["", "stop"]),
            patch("slave.main.time.sleep") as mock_sleep,
        ):
            result = _run_guardian(Path("C:/TA/TriangleAlpha-Slave.exe"))

        assert result == 0
        assert mock_spawn.call_count == 2
        mock_sleep.assert_called_once_with(1.0)
        mock_lock.release.assert_called_once()
