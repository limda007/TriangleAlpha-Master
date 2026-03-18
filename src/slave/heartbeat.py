"""UDP 心跳广播"""
from __future__ import annotations

import asyncio
import os
import platform
import socket

import psutil

from common.protocol import HEARTBEAT_INTERVAL, UDP_PORT, build_udp_ext_online, build_udp_offline

SLAVE_VERSION = "2.0.0"


class HeartbeatService:
    def __init__(
        self,
        master_ip: str | None = None,
        port: int = UDP_PORT,
        interval: int = HEARTBEAT_INTERVAL,
    ):
        self._master_ip = master_ip
        self._port = port
        self._interval = interval
        self._machine_name = platform.node()
        self._user_name = os.getenv("USERNAME", os.getenv("USER", "unknown"))
        self._group = "默认"
        self._running = False

    @property
    def machine_name(self) -> str:
        return self._machine_name

    def set_group(self, group: str) -> None:
        self._group = group

    async def run(self) -> None:
        self._running = True
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        loop = asyncio.get_event_loop()

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
            except Exception:
                pass
            await asyncio.sleep(self._interval)

        # 发送离线通知
        try:
            offline = build_udp_offline(self._machine_name).encode("utf-8")
            target = (self._master_ip, self._port) if self._master_ip else ("255.255.255.255", self._port)
            await loop.sock_sendto(sock, offline, target)
        except Exception:
            pass
        sock.close()

    def stop(self) -> None:
        self._running = False
