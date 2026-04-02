"""​TCP 指令发送器：通过线程池并发发送 TCP 命令到被控端"""
from __future__ import annotations

import errno
import logging
import os
import socket

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from common.protocol import TCP_CMD_PORT, TCP_SEND_TIMEOUT, TcpCommand, build_tcp_command

logger = logging.getLogger(__name__)


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
            cmd_type = self._command_str.split("|", 1)[0]
            logger.info("[TCP发送] %s:%d ← %s", self._ip, TCP_CMD_PORT, cmd_type)
            self._commander.command_sent.emit(self._ip, self._command_str)
        except OSError as e:
            if self._is_expected_self_update_disconnect(e):
                self._commander.command_sent.emit(self._ip, self._command_str)
            else:
                logger.error("[TCP发送失败] %s:%d - %s", self._ip, TCP_CMD_PORT, e)
                self._commander.command_failed.emit(self._ip, str(e))
        finally:
            sock.close()

    def _is_expected_self_update_disconnect(self, err: Exception) -> bool:
        if not self._command_str.startswith(f"{TcpCommand.UPDATE_SELF.value}|"):
            return False
        if isinstance(err, (BrokenPipeError, ConnectionResetError)):
            return True
        err_no = getattr(err, "errno", None)
        return err_no in {errno.EPIPE, errno.ECONNRESET, 54, 10054}


class TcpCommander(QObject):
    """通过线程池并发发送 TCP 指令到被控端"""

    command_sent = pyqtSignal(str, str)    # (ip, command_str)
    command_failed = pyqtSignal(str, str)  # (ip, error_msg)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # P1: 限制并发连接数，防止线程爆炸
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(min(32, (os.cpu_count() or 4) * 4))

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

    def stop(self, timeout_ms: int = 3000) -> None:
        """等待所有发送任务完成并关闭线程池"""
        self._pool.waitForDone(timeout_ms)
