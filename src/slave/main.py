"""被控端入口"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from slave.auto_setup import check_rename, kill_remote_controls, setup_startup
from slave.command_handler import CommandHandler
from slave.heartbeat import HeartbeatService
from slave.log_reporter import LogReporter


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
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


async def _main() -> None:
    base_dir = _get_base_dir()
    master_ip = _read_master_ip(base_dir)

    print("=" * 50)
    print("  TriangleAlpha 被控端 v2.0.0")
    print("=" * 50)
    print(f"  目录: {base_dir}")
    print(f"  主控: {master_ip or '广播模式'}")
    print()

    # 自动配置
    setup_startup()
    check_rename(base_dir)

    # 核心服务
    heartbeat = HeartbeatService(master_ip=master_ip)
    handler = CommandHandler(str(base_dir))
    log_reporter = LogReporter(master_ip, heartbeat.machine_name)

    # 安装日志拦截（stdout → TCP 上报）
    log_reporter.install()

    # 分组变更回调
    handler.set_group_callback(heartbeat.set_group)

    print("[就绪] 心跳间隔 3s，TCP 监听 9999，日志上报已启用")
    print()

    await asyncio.gather(
        heartbeat.run(),
        handler.run(),
        log_reporter.run(),
        kill_remote_controls(base_dir),
    )


def main() -> None:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\n[退出] 正在关闭...")


if __name__ == "__main__":
    main()
