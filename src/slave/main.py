"""被控端入口 — PyQt6 GUI + asyncio 后台服务"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

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

    exit_code = app.exec()
    backend.stop()
    backend.wait(5000)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
