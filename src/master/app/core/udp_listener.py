"""UDP 广播监听线程：接收被控端心跳和状态消息"""
from __future__ import annotations

import socket

from PyQt6.QtCore import QThread, pyqtSignal

from common.protocol import UDP_PORT, parse_udp_message


class UdpListenerThread(QThread):
    """后台线程，绑定 UDP 端口接收被控端广播消息"""

    message_received = pyqtSignal(object, str)  # (UdpMessage, remote_ip)

    def __init__(self, port: int = UDP_PORT, parent: QThread | None = None) -> None:
        super().__init__(parent)
        self._port = port
        self._running = False

    def run(self) -> None:
        self._running = True
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # P0: 1MB 接收缓冲区，防止 100+ 节点高并发心跳丢包
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        # P0: 缩短超时，提高消息响应频率
        sock.settimeout(0.5)
        try:
            sock.bind(("", self._port))
            while self._running:
                try:
                    data, addr = sock.recvfrom(4096)
                except TimeoutError:
                    continue
                raw = data.decode("utf-8", errors="ignore").strip()
                if not raw:
                    continue
                msg = parse_udp_message(raw)
                if msg is not None:
                    self.message_received.emit(msg, addr[0])
        finally:
            sock.close()

    def stop(self) -> None:
        """请求停止并等待线程结束"""
        self._running = False
        self.wait(5000)
