"""本地 UDP IPC 监听器 — 接收 TestDemo.exe 实时状态上报（端口 8889）。

原始 C# 被控架构：TestDemo.exe 通过本地 UDP 发送
STATUS|<ignored>|{account}|{level}|{jinbi}|{status_text}|{elapsed}
本模块复刻该监听逻辑，缓存最新数据供 _status_reporter 使用。
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from common.protocol import LOCAL_IPC_PORT
from slave.logging_utils import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)


def parse_ipc_status(data: bytes) -> dict[str, str] | None:
    """解析 TestDemo 本地 IPC 消息。

    格式: STATUS|<ignored>|{account}|{level}|{jinbi}|{status_text}|{elapsed}
    parts[1] 是 TestDemo 内部标识，原始 C# 被控完全忽略。
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    parts = text.split("|")
    if len(parts) < 7 or parts[0] != "STATUS":
        return None
    return {
        "account": parts[2],
        "level": parts[3],
        "jinbi": parts[4],
        "status_text": parts[5],
        "elapsed": parts[6],
    }


class _IpcProtocol(asyncio.DatagramProtocol):
    """asyncio DatagramProtocol，将收到的 UDP 包交给回调处理。"""

    def __init__(self, callback: Callable[[dict[str, str]], None]) -> None:
        self._callback = callback

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        parsed = parse_ipc_status(data)
        if parsed is not None:
            self._callback(parsed)


class LocalIpcReceiver:
    """监听本地 UDP 端口，缓存 TestDemo 推送的最新状态。

    Usage::

        receiver = LocalIpcReceiver()
        asyncio.create_task(receiver.run())
        # 在其他协程中读取:
        data, age = receiver.snapshot()
    """

    def __init__(self, port: int = LOCAL_IPC_PORT) -> None:
        self._port = port
        self._data: dict[str, str] | None = None
        self._last_sync: float = 0.0

    async def run(self) -> None:
        """启动 UDP 监听（阻塞直到取消）。"""
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _IpcProtocol(self._on_message),
            local_addr=("0.0.0.0", self._port),
        )
        logger.info("本地 IPC 监听已启动 (UDP:%d)", self._port)
        try:
            await asyncio.Event().wait()
        finally:
            transport.close()
            logger.info("本地 IPC 监听已关闭")

    def snapshot(self) -> tuple[dict[str, str] | None, float]:
        """返回 (最新IPC数据副本, 距上次更新的秒数)。

        无数据时返回 (None, inf)。数据是副本，修改不影响内部状态。
        """
        if self._data is None:
            return None, float("inf")
        age = time.monotonic() - self._last_sync
        return dict(self._data), age

    def _on_message(self, data: dict[str, str]) -> None:
        self._data = data
        self._last_sync = time.monotonic()
        logger.debug(
            "IPC 收到: account=%s level=%s status=%s",
            data.get("account", ""),
            data.get("level", ""),
            data.get("status_text", ""),
        )
