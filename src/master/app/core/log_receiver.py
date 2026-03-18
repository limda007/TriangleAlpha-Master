"""日志接收线程 — TCP 8890 接收被控端日志"""
from __future__ import annotations

import socket
import traceback

from PyQt6.QtCore import QThread, pyqtSignal

from common.protocol import TCP_LOG_PORT


class LogEntry:
    """一条日志"""
    __slots__ = ("machine_name", "timestamp", "level", "content")

    def __init__(self, machine_name: str, timestamp: str, level: str, content: str):
        self.machine_name = machine_name
        self.timestamp = timestamp
        self.level = level
        self.content = content


class LogReceiverThread(QThread):
    """TCP 服务器线程，接收被控端的 LOG| 消息"""

    log_received = pyqtSignal(object)  # LogEntry
    error_occurred = pyqtSignal(str)

    def __init__(self, port: int = TCP_LOG_PORT, parent=None):
        super().__init__(parent)
        self._port = port
        self._running = True

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
                conn.settimeout(5.0)
                data = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"\n" in data:
                        break
                conn.close()

                for line in data.decode("utf-8", errors="ignore").strip().splitlines():
                    self._parse_line(line)
            except TimeoutError:
                continue
            except Exception:
                if self._running:
                    self.error_occurred.emit(traceback.format_exc())

        srv.close()

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
        self.wait(3000)
