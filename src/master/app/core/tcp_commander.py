"""​TCP 指令发送器：通过线程池并发发送 TCP 命令到被控端"""
from __future__ import annotations

import errno
import logging
import os
import random
import socket
import time

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from common.protocol import TCP_CMD_PORT, TCP_SEND_TIMEOUT, TcpCommand, build_tcp_command

logger = logging.getLogger(__name__)

TCP_ACK_TIMEOUT = 2.0
TCP_RETRY_MAX_ATTEMPTS = 4
TCP_DELETE_RETRY_MAX_ATTEMPTS = 2
TCP_RETRY_BASE_DELAY_S = 0.5
TCP_RETRY_MAX_DELAY_S = 8.0
TCP_RETRY_JITTER_RATIO = 0.2
_RETRIABLE_AGENT_ERROR_CODES = {"io_error", "timeout", "temporary_failure", "busy"}
_NON_RETRY_COMMANDS = {TcpCommand.REBOOT_PC.value, TcpCommand.UPDATE_SELF.value}


class _TcpCommandFailure(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class _TcpSendTask(QRunnable):
    """线程池任务：发送一条 TCP 命令"""

    def __init__(self, ip: str, command_str: str, commander: TcpCommander, *, require_ack: bool = False) -> None:
        super().__init__()
        self._ip = ip
        self._command_str = command_str
        self._commander = commander
        self._require_ack = require_ack
        self.setAutoDelete(True)

    def run(self) -> None:
        attempts = self._max_attempts()
        last_error = ""
        for attempt in range(attempts):
            try:
                self._send_once()
                self._commander.command_sent.emit(self._ip, self._command_str)
                return
            except OSError as err:
                if self._is_expected_self_update_disconnect(err):
                    self._commander.command_sent.emit(self._ip, self._command_str)
                    return
                last_error = str(err)
                retryable = True
            except _TcpCommandFailure as err:
                last_error = str(err)
                retryable = err.retryable

            if retryable and attempt < attempts - 1 and self._can_retry_command():
                delay_s = self._retry_delay_s(attempt)
                logger.warning(
                    "[TCP发送重试] %s:%d 第%s/%s次失败: %s，%.2fs 后重试",
                    self._ip,
                    TCP_CMD_PORT,
                    attempt + 1,
                    attempts,
                    last_error,
                    delay_s,
                )
                time.sleep(delay_s)
                continue
            break

        logger.error("[TCP发送失败] %s:%d - %s", self._ip, TCP_CMD_PORT, last_error)
        self._commander.command_failed.emit(self._ip, last_error)

    def _send_once(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(TCP_SEND_TIMEOUT)
            sock.connect((self._ip, TCP_CMD_PORT))
            sock.sendall((self._command_str + "\n").encode("utf-8"))
            ack = self._read_ack(sock)
            cmd_type = self._command_str.split("|", 1)[0]
            if ack is None:
                logger.info("[TCP发送] %s:%d ← %s，未收到 ACK，按旧协议兼容成功", self._ip, TCP_CMD_PORT, cmd_type)
                return
            self._handle_ack(ack)
            logger.info("[TCP发送] %s:%d ← %s ACK=%s", self._ip, TCP_CMD_PORT, cmd_type, ack)
        finally:
            sock.close()

    def _read_ack(self, sock: socket.socket) -> str | None:
        chunks: list[bytes] = []
        sock.settimeout(TCP_ACK_TIMEOUT)
        try:
            while sum(len(chunk) for chunk in chunks) < 4096:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        except TimeoutError as err:
            if self._require_ack:
                raise _TcpCommandFailure("等待 TCP ACK 超时", retryable=True) from err
            return None
        except socket.timeout as err:
            if self._require_ack:
                raise _TcpCommandFailure("等待 TCP ACK 超时", retryable=True) from err
            return None

        if not chunks:
            if self._require_ack:
                raise _TcpCommandFailure("未收到 TCP ACK", retryable=True)
            return None
        return b"".join(chunks).decode("utf-8", errors="replace").strip()

    def _handle_ack(self, ack: str) -> None:
        if ack == "OK":
            return
        if ack.startswith("ERR|"):
            _, _, rest = ack.partition("|")
            code, _, message = rest.partition("|")
            code = code or "agent_error"
            retryable = code in _RETRIABLE_AGENT_ERROR_CODES
            raise _TcpCommandFailure(f"{code}: {message or 'Agent 返回失败'}", retryable=retryable)
        raise _TcpCommandFailure(f"无法识别的 TCP ACK: {ack[:120]}", retryable=True)

    def _command_type(self) -> str:
        return self._command_str.split("|", 1)[0]

    def _can_retry_command(self) -> bool:
        return self._command_type() not in _NON_RETRY_COMMANDS

    def _max_attempts(self) -> int:
        if not self._can_retry_command():
            return 1
        if self._command_type() == TcpCommand.DELETE_FILE.value:
            return TCP_DELETE_RETRY_MAX_ATTEMPTS
        return TCP_RETRY_MAX_ATTEMPTS

    def _retry_delay_s(self, attempt: int) -> float:
        delay = min(TCP_RETRY_BASE_DELAY_S * (2**attempt), TCP_RETRY_MAX_DELAY_S)
        jitter = delay * TCP_RETRY_JITTER_RATIO
        return max(0.0, delay + random.uniform(-jitter, jitter))

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

    def send(self, ip: str, cmd: TcpCommand, payload: str = "", *, require_ack: bool = False) -> None:
        """发送单条 TCP 命令"""
        command_str = build_tcp_command(cmd, payload)
        task = _TcpSendTask(ip, command_str, self, require_ack=require_ack)
        self._pool.start(task)

    def broadcast(self, ips: list[str], cmd: TcpCommand, payload: str = "", *, require_ack: bool = False) -> None:
        """向多个 IP 广播同一命令"""
        command_str = build_tcp_command(cmd, payload)
        for ip in ips:
            task = _TcpSendTask(ip, command_str, self, require_ack=require_ack)
            self._pool.start(task)

    def stop(self, timeout_ms: int = 3000) -> None:
        """等待所有发送任务完成并关闭线程池"""
        self._pool.waitForDone(timeout_ms)
