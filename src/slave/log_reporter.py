"""日志上报 — 将 Console 输出通过 TCP 发送给中控"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import queue as thread_queue
import sys
from datetime import datetime
from typing import IO

from common.protocol import TCP_LOG_PORT


class LogReporter:
    """捕获 stdout 并通过 TCP 转发到中控端"""

    def __init__(self, master_ip: str | None, machine_name: str,
                 port: int = TCP_LOG_PORT):
        self._master_ip = master_ip
        self._machine_name = machine_name
        self._port = port
        # C2: 使用线程安全的 queue.Queue 替代 asyncio.Queue
        self._queue: thread_queue.Queue[str] = thread_queue.Queue(maxsize=1000)
        self._running = False
        self._original_stdout: IO[str] | None = None

    def install(self) -> None:
        """安装 stdout 拦截器（兼容 console=False 时 stdout=None）"""
        self._original_stdout = sys.stdout
        sys.stdout = _TeeWriter(self._original_stdout, self._queue, self._machine_name)  # type: ignore[assignment]

    async def run(self) -> None:
        """消费队列，批量发送日志到中控"""
        # H3: 无主控 IP 时直接返回，不再死循环
        if not self._master_ip:
            return

        self._running = True
        loop = asyncio.get_running_loop()
        while self._running:
            lines: list[str] = []
            # C2: 通过 run_in_executor 桥接线程安全队列的阻塞 get
            try:
                line = await loop.run_in_executor(
                    None, functools.partial(self._queue.get, timeout=5.0),
                )
                lines.append(line)
            except thread_queue.Empty:
                continue

            # 批量取剩余
            while not self._queue.empty() and len(lines) < 50:
                try:
                    lines.append(self._queue.get_nowait())
                except thread_queue.Empty:
                    break

            # 发送
            await self._send_lines(lines)

    async def _send_lines(self, lines: list[str]) -> None:
        """发送日志行到中控，带重试"""
        for attempt in range(3):
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._master_ip, self._port),
                    timeout=5.0,
                )
                payload = "\n".join(lines) + "\n"
                writer.write(payload.encode("utf-8"))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
        # M5: 重试耗尽，丢弃日志（推回队列无法保证顺序，不如丢弃）

    def stop(self) -> None:
        self._running = False
        if self._original_stdout is not None:
            sys.stdout = self._original_stdout


class _TeeWriter:
    """同时写入原始 stdout 和线程安全队列

    注意: 不继承 io.TextIOBase 以避免 C5 encoding 属性 LSP 冲突。
    sys.stdout 赋值处已有 type: ignore[assignment]。
    """

    def __init__(self, original: IO[str] | None, q: thread_queue.Queue[str], machine_name: str):
        self._original = original
        self._queue = q
        self._machine_name = machine_name
        # C5: 直接作为实例属性，避免 @property 覆盖父类可写属性
        self.encoding: str = getattr(original, "encoding", "utf-8") or "utf-8"

    def write(self, text: str) -> int:
        if self._original is not None:
            self._original.write(text)
        # 按行拆分，构造 LOG|... 消息
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            ts = datetime.now().strftime("%H:%M:%S")
            level = "INFO"
            if "[错误]" in stripped or "[异常]" in stripped or "ERROR" in stripped:
                level = "ERROR"
            elif "[警告]" in stripped or "WARN" in stripped:
                level = "WARN"
            msg = f"LOG|{self._machine_name}|{ts}|{level}|{stripped}"
            # C2: thread_queue.Queue.put_nowait 是线程安全的
            with contextlib.suppress(thread_queue.Full):
                self._queue.put_nowait(msg)
        return len(text)

    def flush(self) -> None:
        if self._original is not None:
            self._original.flush()

    def fileno(self) -> int:
        if self._original is not None:
            return self._original.fileno()
        raise OSError("no underlying fileno")

    @property
    def writable(self) -> bool:
        return True
