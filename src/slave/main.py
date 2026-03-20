"""被控端入口 — PyQt6 GUI + asyncio 后台服务"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path

import psutil
from PyQt6.QtWidgets import QApplication, QMessageBox

from slave.backend import SlaveBackend
from slave.slave_window import SlaveWindow


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # 源码模式: 优先使用 CWD（运维通常 cd 到部署目录再启动）
    cwd = Path.cwd()
    if (cwd / "TestDemo.exe").exists() or (cwd / "主控IP.txt").exists() or (cwd / "master.txt").exists():
        return cwd
    return Path(__file__).parent


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
                print(f"[配置] 主控IP: {ip} (来自 {name})")
                return ip
    return None


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 托盘模式

    base_dir = _get_base_dir()

    # 单实例保护：全局锁文件（放在系统临时目录，不受工作目录影响）
    pid_path = Path(tempfile.gettempdir()) / "TriangleAlphaSlave.pid"
    instance_lock = acquire_instance_lock(pid_path)
    if instance_lock is None:
        QMessageBox.warning(None, "TA-Slave", "已有实例在运行中，请勿重复启动。")
        sys.exit(0)

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

    backend.start()
    window.show()

    try:
        exit_code = app.exec()
    finally:
        backend.stop()
        if not backend.wait(5000):
            print("[警告] SlaveBackend 未在 5 秒内停止")
        instance_lock.release()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
