"""被控端入口 — PyQt6 GUI + asyncio 后台服务"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from slave.backend import SlaveBackend
from slave.logging_utils import configure_slave_logging, get_logger
from slave.runtime_paths import RESOURCE_DIR, get_base_dir
from slave.slave_window import SlaveWindow

logger = get_logger(__name__)
SLAVE_CLIENT_CONSOLE_FILENAME = "SlaveClientConsole.exe"
_SLAVE_CHILD_FLAG = "--child"
_SLAVE_GUARD_FLAG = "--guard"
_GUARD_ACTION_STOP = "stop"
_CONSOLE_PLACEHOLDER_POLL_SEC = 2.0
_CONSOLE_PLACEHOLDER_MAX_WAIT_SEC = 12 * 60 * 60
_CONSOLE_PLACEHOLDER_MAX_SIZE = 1024 * 1024
_CSC_CANDIDATE_PATHS = (
    Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
)
_CONSOLE_PLACEHOLDER_SOURCE = r"""
using System;
using System.Diagnostics;
using System.Management;

internal static class Program
{
    private const int MaxWaitMs = 12 * 60 * 60 * 1000;

    private static int Main(string[] args)
    {
        try
        {
            int? parentPid = GetParentProcessId();
            if (parentPid.HasValue)
            {
                try
                {
                    using (Process parent = Process.GetProcessById(parentPid.Value))
                    {
                        parent.WaitForExit(MaxWaitMs);
                    }
                }
                catch
                {
                }
            }
        }
        catch
        {
        }

        return 0;
    }

    private static int? GetParentProcessId()
    {
        string query = "win32_process.handle='"
            + Process.GetCurrentProcess().Id
            + "'";
        using (ManagementObject current = new ManagementObject(query))
        {
            current.Get();
            object raw = current["ParentProcessId"];
            if (raw == null)
            {
                return null;
            }

            return Convert.ToInt32(raw);
        }
    }
}
"""
_GUARD_RESTART_DELAY_SEC = 3.0
_GUARD_MAX_RESTART_DELAY_SEC = 30.0
_GUARD_CHILD_BUSY_EXIT_CODE = 75
_GUARD_CHILD_BUSY_DELAY_SEC = 1.0


def _get_base_dir() -> Path:
    """兼容旧测试与旧调用点。"""
    return get_base_dir()


def _is_real_qt_app(app: object) -> bool:
    return app.__class__.__module__.startswith("PyQt6.")


def _guard_lock_path() -> Path:
    return Path(tempfile.gettempdir()) / "TriangleAlphaSlave.guard.pid"


def _guard_action_path() -> Path:
    return Path(tempfile.gettempdir()) / "TriangleAlphaSlave.guard.action"


def _should_use_guardian() -> bool:
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def _is_guard_child_mode() -> bool:
    return _SLAVE_CHILD_FLAG in sys.argv


def _is_guard_process_mode() -> bool:
    return _SLAVE_GUARD_FLAG in sys.argv


def _build_child_spawn_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for key in list(env):
        if key.startswith("_PYI_"):
            env.pop(key, None)
    return env


def _write_guard_action(action: str) -> None:
    _guard_action_path().write_text(action, encoding="utf-8")


def _consume_guard_action() -> str:
    action_path = _guard_action_path()
    try:
        action = action_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError:
        logger.exception("读取守护动作失败: %s", action_path)
        return ""
    with contextlib.suppress(OSError):
        action_path.unlink()
    return action


def _clear_guard_action() -> None:
    with contextlib.suppress(OSError):
        _guard_action_path().unlink()


def _notify_guard_stop() -> None:
    if not _is_guard_child_mode():
        return
    with contextlib.suppress(OSError):
        _write_guard_action(_GUARD_ACTION_STOP)


class InstanceLock:
    def __init__(self, pid_path: Path, fd: int) -> None:
        self._pid_path = pid_path
        self._fd: int | None = fd
        self._pid = os.getpid()

    def release(self) -> None:
        if self._fd is None:
            return
        os.close(self._fd)
        self._fd = None
        if _read_lock_pid(self._pid_path) != self._pid:
            return
        with contextlib.suppress(FileNotFoundError):
            self._pid_path.unlink()



def _read_lock_pid(pid_path: Path) -> int | None:
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None



def _is_pid_active(pid: int) -> bool:
    return pid != os.getpid() and psutil.pid_exists(pid)



def acquire_instance_lock(pid_path: Path) -> InstanceLock | None:
    while True:
        try:
            fd = os.open(pid_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            old_pid = _read_lock_pid(pid_path)
            if old_pid is not None and _is_pid_active(old_pid):
                return None
            with contextlib.suppress(FileNotFoundError):
                pid_path.unlink()
            continue
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as fh:
            fh.write(str(os.getpid()))
        return InstanceLock(pid_path, fd)


def _read_master_ip(base_dir: Path) -> str | None:
    for name in ("主控IP.txt", "master.txt"):
        p = base_dir / name
        if p.exists():
            ip = p.read_text(encoding="utf-8").strip()
            if ip:
                logger.info("主控IP: %s (来自 %s)", ip, name)
                return ip
    return None


def _current_executable_path() -> Path | None:
    raw = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    if not raw:
        return None
    return Path(raw)


def _is_console_placeholder_mode(executable_path: Path | None = None) -> bool:
    path = executable_path or _current_executable_path()
    return path is not None and path.name.lower() == SLAVE_CLIENT_CONSOLE_FILENAME.lower()


def _find_csc() -> str | None:
    csc_path = shutil.which("csc")
    if csc_path:
        return csc_path
    for candidate in _CSC_CANDIDATE_PATHS:
        if candidate.exists():
            return str(candidate)
    return None


def _is_small_console_placeholder(placeholder_path: Path) -> bool:
    try:
        if placeholder_path.stat().st_size > _CONSOLE_PLACEHOLDER_MAX_SIZE:
            return False
        with placeholder_path.open("rb") as fh:
            return fh.read(2) == b"MZ"
    except OSError:
        return False


def _is_fresh_console_placeholder(source_path: Path, placeholder_path: Path) -> bool:
    if not _is_small_console_placeholder(placeholder_path):
        return False
    try:
        return placeholder_path.stat().st_mtime >= source_path.stat().st_mtime
    except OSError:
        return False


def _build_console_placeholder_stub(placeholder_path: Path) -> bool:
    csc_path = _find_csc()
    if csc_path is None:
        return False

    temp_path = placeholder_path.with_name(f"{placeholder_path.name}.tmp")
    try:
        with tempfile.TemporaryDirectory(prefix="trianglealpha-slave-stub-") as tmp_dir:
            source_path = Path(tmp_dir) / "SlaveClientConsole.cs"
            source_path.write_text(_CONSOLE_PLACEHOLDER_SOURCE, encoding="utf-8")
            cmd = [
                csc_path,
                "/nologo",
                "/target:winexe",
                "/optimize+",
                "/platform:anycpu",
                "/r:System.Management.dll",
                f"/out:{temp_path}",
                str(source_path),
            ]
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
        temp_path.replace(placeholder_path)
    except (OSError, subprocess.CalledProcessError):
        temp_path.unlink(missing_ok=True)
        logger.exception("编译占位程序失败: %s", placeholder_path)
        return False

    logger.info("占位程序已编译: %s", placeholder_path)
    return True


def _ensure_console_placeholder(current_executable: Path | None = None) -> Path | None:
    """确保 TestDemo 兼容占位程序存在，优先生成小 stub，失败时回退复制。"""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    source_path = current_executable if current_executable is not None else _current_executable_path()
    if source_path is None or _is_console_placeholder_mode(source_path):
        return None

    placeholder_path = source_path.parent / SLAVE_CLIENT_CONSOLE_FILENAME
    if _is_fresh_console_placeholder(source_path, placeholder_path):
        return placeholder_path

    if _build_console_placeholder_stub(placeholder_path):
        return placeholder_path

    temp_path = placeholder_path.with_suffix(f"{placeholder_path.suffix}.tmp")
    try:
        shutil.copy2(source_path, temp_path)
        temp_path.replace(placeholder_path)
    except OSError:
        temp_path.unlink(missing_ok=True)
        logger.exception("修复占位程序失败: %s", placeholder_path)
        return None

    logger.info("占位程序已就绪: %s", placeholder_path)
    return placeholder_path


def _run_console_placeholder(
    *,
    parent_pid: int | None = None,
    poll_interval_sec: float = _CONSOLE_PLACEHOLDER_POLL_SEC,
    max_wait_sec: float = _CONSOLE_PLACEHOLDER_MAX_WAIT_SEC,
) -> int:
    """作为 TestDemo 占位程序运行，避免拉起第二个完整 slave GUI。"""
    wait_pid = parent_pid if parent_pid is not None else os.getppid()
    if wait_pid <= 0 or wait_pid == os.getpid():
        return 0

    deadline = time.monotonic() + max_wait_sec if max_wait_sec > 0 else None
    while psutil.pid_exists(wait_pid):
        if deadline is not None and time.monotonic() >= deadline:
            break
        time.sleep(max(0.1, poll_interval_sec))
    return 0


def _spawn_guarded_child(executable_path: Path) -> subprocess.Popen[bytes]:
    creation_flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    return subprocess.Popen(  # noqa: S603
        [str(executable_path), _SLAVE_CHILD_FLAG],
        cwd=str(executable_path.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
        env=_build_child_spawn_env(),
    )


def _run_guardian(executable_path: Path | None = None) -> int:
    if not _should_use_guardian():
        return _run_slave_app()

    current_executable = executable_path if executable_path is not None else _current_executable_path()
    if current_executable is None:
        logger.error("无法确定当前 slave 可执行文件路径，跳过守护模式")
        return _run_slave_app()

    guard_lock = acquire_instance_lock(_guard_lock_path())
    if guard_lock is None:
        return 0

    restart_delay = _GUARD_RESTART_DELAY_SEC
    try:
        while True:
            _clear_guard_action()
            start_monotonic = time.monotonic()
            try:
                child = _spawn_guarded_child(current_executable)
            except OSError:
                logger.exception("启动 slave 子进程失败")
                time.sleep(restart_delay)
                restart_delay = min(restart_delay * 2, _GUARD_MAX_RESTART_DELAY_SEC)
                continue

            exit_code = child.wait()
            action = _consume_guard_action()
            if action == _GUARD_ACTION_STOP:
                return exit_code
            if exit_code == _GUARD_CHILD_BUSY_EXIT_CODE:
                logger.info("Slave 子进程启动时检测到旧实例仍在退出，%.1fs 后重试", _GUARD_CHILD_BUSY_DELAY_SEC)
                time.sleep(_GUARD_CHILD_BUSY_DELAY_SEC)
                restart_delay = _GUARD_RESTART_DELAY_SEC
                continue

            lifetime = time.monotonic() - start_monotonic
            if lifetime >= 60:
                restart_delay = _GUARD_RESTART_DELAY_SEC
            logger.warning(
                "Slave 子进程异常退出: exit_code=%s lifetime=%.1fs，%ss 后重启",
                exit_code,
                lifetime,
                restart_delay,
            )
            time.sleep(restart_delay)
            restart_delay = min(restart_delay * 2, _GUARD_MAX_RESTART_DELAY_SEC)
    finally:
        guard_lock.release()


def _run_slave_app() -> int:
    configure_slave_logging()
    _ensure_console_placeholder()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 托盘模式
    app.aboutToQuit.connect(_notify_guard_stop)  # type: ignore[union-attr]
    icon_path = RESOURCE_DIR / "icon.png"
    if icon_path.exists() and _is_real_qt_app(app):
        app.setWindowIcon(QIcon(str(icon_path)))

    base_dir = _get_base_dir()

    # 单实例保护：全局锁文件（放在系统临时目录，不受工作目录影响）
    pid_path = Path(tempfile.gettempdir()) / "TriangleAlphaSlave.pid"
    instance_lock = acquire_instance_lock(pid_path)
    if instance_lock is None:
        if _is_guard_child_mode():
            logger.info("检测到旧实例仍在退出，子进程本次启动跳过，等待 guardian 重试")
            return _GUARD_CHILD_BUSY_EXIT_CODE
        QMessageBox.warning(None, "TA-Slave", "已有实例在运行中，请勿重复启动。")
        return 0

    master_ip = _read_master_ip(base_dir)

    window = SlaveWindow(base_dir, master_ip)
    backend = SlaveBackend(base_dir, master_ip)

    # 信号 → GUI 槽
    backend.heartbeat_sent.connect(window.on_heartbeat)
    backend.command_received.connect(window.on_command)
    backend.account_updated.connect(window.on_account_updated)
    backend.script_status.connect(window.on_script_status)
    backend.group_changed.connect(window.on_group_changed)
    backend.log_entry.connect(window.append_log)
    backend.error_occurred.connect(window.append_log)
    backend.shutdown_requested.connect(app.quit)

    backend.start()
    # 启动时直接最小化到托盘，不弹出窗口

    try:
        return int(app.exec())
    finally:
        backend.stop()
        if not backend.wait(5000):
            logger.warning("SlaveBackend 未在 5 秒内停止")
        instance_lock.release()


def main() -> None:
    # --uninstall: 自清理后退出
    if "--uninstall" in sys.argv:
        from slave.auto_setup import uninstall
        uninstall()
        print("卸载清理完成")
        sys.exit(0)

    if _is_console_placeholder_mode():
        sys.exit(_run_console_placeholder())
    current_executable = _current_executable_path()
    if _is_guard_process_mode() or (_should_use_guardian() and not _is_guard_child_mode()):
        sys.exit(_run_guardian(current_executable))
    sys.exit(_run_slave_app())


if __name__ == "__main__":
    main()
