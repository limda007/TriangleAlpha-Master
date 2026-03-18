"""TCP 指令发送器：通过线程池并发发送 TCP 命令到被控端"""
from __future__ import annotations

import socket

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from common.protocol import TCP_CMD_PORT, TCP_SEND_TIMEOUT, TcpCommand, build_tcp_command


class _TcpSendTask(QRunnable):
    """线程池任务：发送一条 TCP 命令"""

    def __init__(self, ip: str, command_str: str, commander: TcpCommander) -> None:
        super().__init__()
        self._ip = ip
        self._command_str = command_str
        self._commander = commander
        self.setAutoDelete(True)

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(TCP_SEND_TIMEOUT)
            sock.connect((self._ip, TCP_CMD_PORT))
            sock.sendall((self._command_str + "\n").encode("utf-8"))
            self._commander.command_sent.emit(self._ip, self._command_str)
        except Exception as e:  # noqa: BLE001
            self._commander.command_failed.emit(self._ip, str(e))
        finally:
            sock.close()


class TcpCommander(QObject):
    """通过线程池并发发送 TCP 指令到被控端"""

    command_sent = pyqtSignal(str, str)    # (ip, command_str)
    command_failed = pyqtSignal(str, str)  # (ip, error_msg)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool: QThreadPool = QThreadPool.globalInstance()  # type: ignore[assignment]

    def send(self, ip: str, cmd: TcpCommand, payload: str = "") -> None:
        """发送单条 TCP 命令"""
        command_str = build_tcp_command(cmd, payload)
        task = _TcpSendTask(ip, command_str, self)
        self._pool.start(task)

    def broadcast(self, ips: list[str], cmd: TcpCommand, payload: str = "") -> None:
        """向多个 IP 广播同一命令"""
        command_str = build_tcp_command(cmd, payload)
        for ip in ips:
            task = _TcpSendTask(ip, command_str, self)
            self._pool.start(task)
