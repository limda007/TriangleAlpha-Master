"""日志接收线程 — TCP 8890 接收被控端日志"""
from __future__ import annotations

import logging
import os
import socket
import traceback
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QThread, pyqtSignal

from common.protocol import TCP_LOG_PORT

_logger = logging.getLogger(__name__)


class LogEntry:
    """一条日志"""
    __slots__ = ("machine_name", "timestamp", "level", "content")

    def __init__(self, machine_name: str, timestamp: str, level: str, content: str):
        self.machine_name = machine_name
        self.timestamp = timestamp
        self.level = level
        self.content = content


class LogReceiverThread(QThread):
    """TCP 服务器线程，接收被控端的 LOG| 消息

    使用线程池并发处理连接，避免慢连接阻塞整个日志接收。
    """

    # 跨线程传递 Python 对象：emit 后不得修改对象内容
    log_received = pyqtSignal(object)  # LogEntry
    error_occurred = pyqtSignal(str)

    def __init__(self, port: int = TCP_LOG_PORT, parent=None):
        super().__init__(parent)
        self._port = port
        self._running = True
        self._executor = ThreadPoolExecutor(
            max_workers=min(16, (os.cpu_count() or 4) * 2),
            thread_name_prefix="log-worker",
        )

    def run(self) -> None:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.settimeout(2.0)
            srv.bind(("0.0.0.0", self._port))
            srv.listen(32)
        except OSError as e:
            self.error_occurred.emit(f"日志端口 {self._port} 绑定失败: {e}")
            return

        while self._running:
            try:
                conn, _addr = srv.accept()
                self._executor.submit(self._handle_conn, conn)
            except TimeoutError:
                continue
            except Exception:
                if self._running:
                    self.error_occurred.emit(traceback.format_exc())

        self._executor.shutdown(wait=False)
        srv.close()

    def _handle_conn(self, conn: socket.socket) -> None:
        """在线程池中处理单个客户端连接（支持持久连接多行日志）"""
        MAX_BUF = 1024 * 1024  # 1MB 缓冲区上限，防 OOM
        try:
            conn.settimeout(30.0)
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_BUF:
                    break
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", errors="ignore").strip()
                    if text:
                        self._parse_line(text)
        except Exception:
            _logger.debug("日志连接处理异常", exc_info=True)
        finally:
            conn.close()
        # 处理末尾无换行的残余
        if buf:
            text = buf.decode("utf-8", errors="ignore").strip()
            if text:
                self._parse_line(text)

    def _parse_line(self, line: str) -> None:
        # 格式: LOG|{machine_name}|{timestamp}|{level}|{content}
        if not line.startswith("LOG|"):
            return
        parts = line.split("|", 4)
        if len(parts) < 5:
            return
        entry = LogEntry(
            machine_name=parts[1],
            timestamp=parts[2],
            level=parts[3],
            content=parts[4],
        )
        self.log_received.emit(entry)

    def stop(self) -> None:
        self._running = False
        self.wait(5000)
        self._executor.shutdown(wait=False)
