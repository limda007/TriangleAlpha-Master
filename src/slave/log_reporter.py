"""日志上报 — 将 Console 输出通过 TCP 发送给中控"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import queue as thread_queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
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
        self._queue: thread_queue.Queue[str] = thread_queue.Queue(maxsize=5000)
        self._running = False
        self._original_stdout: IO[str] | None = None
        self._original_stderr: IO[str] | None = None
        # P0: 专用单线程池，不占用默认线程池
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="log-reporter")
        # P1: 持久化 TCP 连接
        self._writer: asyncio.StreamWriter | None = None

    def install(self) -> None:
        """安装 stdout/stderr 拦截器"""
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = _TeeWriter(self._original_stdout, self._queue, self._machine_name)  # type: ignore[assignment]
        sys.stderr = _TeeWriter(self._original_stderr, self._queue, self._machine_name, is_stderr=True)  # type: ignore[assignment]

    async def run(self) -> None:
        """消费队列，批量发送日志到中控"""
        # H3: 无主控 IP 时直接返回，不再死循环
        if not self._master_ip:
            return

        self._running = True
        loop = asyncio.get_running_loop()
        while self._running or not self._queue.empty():
            lines: list[str] = []
            # P0: 使用专用线程池，不阻塞默认线程池
            try:
                line = await loop.run_in_executor(
                    self._executor, functools.partial(self._queue.get, timeout=1.0),
                )
                lines.append(line)
            except thread_queue.Empty:
                continue
            except RuntimeError:
                break

            # 批量取剩余
            while not self._queue.empty() and len(lines) < 50:
                try:
                    lines.append(self._queue.get_nowait())
                except thread_queue.Empty:
                    break

            # 发送
            await self._send_lines(lines)
            self._check_and_warn_drops()

    async def _ensure_connection(self) -> asyncio.StreamWriter | None:
        """P1: 获取或建立持久 TCP 连接"""
        if self._writer is not None:
            if self._writer.is_closing():
                self._writer = None
            else:
                return self._writer
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self._master_ip, self._port),
                timeout=5.0,
            )
            self._writer = writer
            return writer
        except Exception:
            self._writer = None
            return None

    async def _send_lines(self, lines: list[str]) -> None:
        """P1: 通过持久连接发送日志行，断开时重连"""
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        for attempt in range(3):
            writer = await self._ensure_connection()
            if writer is None:
                if attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
                continue
            try:
                writer.write(payload)
                await writer.drain()
                return
            except Exception:
                # 连接断开，重置并重试
                with contextlib.suppress(Exception):
                    writer.close()
                    await writer.wait_closed()
                self._writer = None
                if attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
        # M5: 重试耗尽，丢弃日志

    def _check_and_warn_drops(self) -> None:
        """检查 stdout/stderr tee writer 的丢弃计数，有丢弃时注入告警。"""
        total_drops = 0
        for stream in (sys.stdout, sys.stderr):
            if isinstance(stream, _TeeWriter):
                total_drops += stream.reset_drop_count()
        if total_drops > 0:
            ts = datetime.now().strftime("%H:%M:%S")
            warn_msg = f"LOG|{self._machine_name}|{ts}|WARN|[日志丢弃] 队列溢出，丢弃 {total_drops} 条日志"
            # 本地 logging 兜底（即使队列也满了，至少本地可见）
            logging.getLogger("trianglealpha.log_reporter").warning(
                "[日志丢弃] 队列溢出，丢弃 %d 条日志", total_drops,
            )
            with contextlib.suppress(thread_queue.Full):
                self._queue.put_nowait(warn_msg)

    def _restore_streams(self) -> None:
        """恢复原始 stdout/stderr。"""
        if self._original_stdout is not None:
            sys.stdout = self._original_stdout
        if self._original_stderr is not None:
            sys.stderr = self._original_stderr

    async def stop(self) -> None:
        self._running = False
        self._restore_streams()
        while not self._queue.empty():
            lines: list[str] = []
            while not self._queue.empty() and len(lines) < 50:
                try:
                    lines.append(self._queue.get_nowait())
                except thread_queue.Empty:
                    break
            if lines:
                await self._send_lines(lines)
        # P1: 关闭持久连接和专用线程池
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
                await self._writer.wait_closed()
            self._writer = None
        # 唤醒可能阻塞在 queue.get() 的 executor 线程，避免 shutdown 等待超时
        with contextlib.suppress(thread_queue.Full):
            self._queue.put_nowait("")
        self._executor.shutdown(wait=True)


class _TeeWriter:
    """同时写入原始 stdout 和线程安全队列

    注意: 不继承 io.TextIOBase 以避免 C5 encoding 属性 LSP 冲突。
    sys.stdout 赋值处已有 type: ignore[assignment]。
    """

    def __init__(
        self, original: IO[str] | None, q: thread_queue.Queue[str],
        machine_name: str, *, is_stderr: bool = False,
    ):
        self._original = original
        self._queue = q
        self._machine_name = machine_name
        # C5: 直接作为实例属性，避免 @property 覆盖父类可写属性
        self._is_stderr = is_stderr
        self.encoding: str = getattr(original, "encoding", "utf-8") or "utf-8"
        self._drop_count: int = 0
        self._drop_lock = threading.Lock()

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
            # stderr 输出的级别下限为 ERROR（不被 WARN 关键字降级）
            if self._is_stderr and level != "ERROR":
                level = "ERROR"
            msg = f"LOG|{self._machine_name}|{ts}|{level}|{stripped}"
            # C2: thread_queue.Queue.put_nowait 是线程安全的
            try:
                self._queue.put_nowait(msg)
            except thread_queue.Full:
                with self._drop_lock:
                    self._drop_count += 1
        return len(text)

    @property
    def drop_count(self) -> int:
        return self._drop_count

    def reset_drop_count(self) -> int:
        """返回当前丢弃数并归零（线程安全）。"""
        with self._drop_lock:
            count = self._drop_count
            self._drop_count = 0
            return count

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
