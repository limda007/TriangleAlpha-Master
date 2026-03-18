"""日志上报 — 将 Console 输出通过 TCP 发送给中控"""
from __future__ import annotations

import asyncio
import contextlib
import io
import sys
from datetime import datetime

from common.protocol import TCP_LOG_PORT


class LogReporter:
    """捕获 stdout 并通过 TCP 转发到中控端"""

    def __init__(self, master_ip: str | None, machine_name: str,
                 port: int = TCP_LOG_PORT):
        self._master_ip = master_ip
        self._machine_name = machine_name
        self._port = port
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._running = False

    def install(self) -> None:
        """安装 stdout 拦截器"""
        self._original_stdout = sys.stdout
        sys.stdout = _TeeWriter(self._original_stdout, self._queue, self._machine_name)

    async def run(self) -> None:
        """消费队列，批量发送日志到中控"""
        if not self._master_ip:
            # 无主控IP，不上报
            while True:
                await asyncio.sleep(3600)
            return

        self._running = True
        while self._running:
            lines: list[str] = []
            # 等第一条
            try:
                line = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                lines.append(line)
            except TimeoutError:
                continue

            # 批量取剩余
            while not self._queue.empty() and len(lines) < 50:
                try:
                    lines.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            # 发送
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._master_ip, self._port),
                    timeout=5.0,
                )
                payload = "\n".join(lines) + "\n"
                writer.write(payload.encode("utf-8"))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass  # 发送失败静默处理

    def stop(self) -> None:
        self._running = False
        if hasattr(self, "_original_stdout"):
            sys.stdout = self._original_stdout


class _TeeWriter(io.TextIOBase):
    """同时写入原始 stdout 和队列"""

    def __init__(self, original: io.TextIOBase, queue: asyncio.Queue, machine_name: str):
        self._original = original
        self._queue = queue
        self._machine_name = machine_name

    def write(self, text: str) -> int:
        self._original.write(text)
        # 按行拆分，构造 LOG|... 消息
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            ts = datetime.now().strftime("%H:%M:%S")
            level = "INFO"
            if "[错误]" in line or "[异常]" in line or "ERROR" in line:
                level = "ERROR"
            elif "[警告]" in line or "WARN" in line:
                level = "WARN"
            msg = f"LOG|{self._machine_name}|{ts}|{level}|{line}"
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(msg)
        return len(text)

    def flush(self) -> None:
        self._original.flush()

    @property
    def encoding(self) -> str:
        return getattr(self._original, "encoding", "utf-8")
