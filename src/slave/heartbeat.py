"""UDP 心跳广播"""
from __future__ import annotations

import asyncio
import os
import platform
import socket
from collections.abc import Callable

import psutil

from common.protocol import HEARTBEAT_INTERVAL, UDP_PORT, build_udp_ext_online, build_udp_offline

SLAVE_VERSION = "2.0.0"


class HeartbeatService:
    def __init__(
        self,
        master_ip: str | None = None,
        port: int = UDP_PORT,
        interval: int = HEARTBEAT_INTERVAL,
        on_sent: Callable[[int, float, float], None] | None = None,
    ):
        self._master_ip = master_ip
        self._port = port
        self._interval = interval
        self._machine_name = platform.node()
        self._user_name = os.getenv("USERNAME", os.getenv("USER", "unknown"))
        self._group = "默认"
        self._running = False
        self._on_sent = on_sent
        self._beat_count = 0
        # P0: 预热 CPU 采样基准值，避免首次 interval=0 返回 0.0
        psutil.cpu_percent()

    @property
    def machine_name(self) -> str:
        return self._machine_name

    def set_group(self, group: str) -> None:
        self._group = group

    async def run(self) -> None:
        self._running = True
        # H2: 使用 with 管理 socket，CancelledError 时也能正确关闭
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
            # M1: 使用 get_running_loop() 替代已弃用的 get_event_loop()
            loop = asyncio.get_running_loop()
            consecutive_errors = 0

            try:
                while self._running:
                    try:
                        cpu = psutil.cpu_percent(interval=0)
                        mem = psutil.virtual_memory().percent
                        msg = build_udp_ext_online(
                            self._machine_name,
                            self._user_name,
                            cpu,
                            mem,
                            SLAVE_VERSION,
                            self._group,
                        )
                        data = msg.encode("utf-8")
                        target = (self._master_ip, self._port) if self._master_ip else ("255.255.255.255", self._port)
                        await loop.sock_sendto(sock, data, target)
                        self._beat_count += 1
                        if self._on_sent:
                            self._on_sent(self._beat_count, cpu, mem)
                        consecutive_errors = 0
                    except Exception as e:
                        consecutive_errors += 1
                        print(f"[心跳] 发送失败 (连续第 {consecutive_errors} 次): {e}")
                        if consecutive_errors >= 10:
                            print("[心跳] 连续失败过多，等待 30 秒后重试")
                            await asyncio.sleep(30)
                            consecutive_errors = 0
                    await asyncio.sleep(self._interval)
            finally:
                # 发送离线通知（尽力而为）
                try:
                    sock.setblocking(True)
                    offline = build_udp_offline(self._machine_name).encode("utf-8")
                    target = (self._master_ip, self._port) if self._master_ip else ("255.255.255.255", self._port)
                    sock.sendto(offline, target)
                except Exception:
                    pass

    def stop(self) -> None:
        self._running = False
