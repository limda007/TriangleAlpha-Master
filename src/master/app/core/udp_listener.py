"""UDP 广播监听线程：接收被控端心跳和状态消息"""
from __future__ import annotations

import errno
import platform
import socket

from PyQt6.QtCore import QThread, pyqtSignal

from common.protocol import (
    UDP_PORT,
    build_udp_master_here,
    parse_discover_master,
    parse_udp_message,
    should_reply_to_discovery,
)

_MAX_UDP_PACKET_SIZE = 65_535
_WSAEMSGSIZE = 10040
# Windows ICMP Port Unreachable: 上一次 sendto 给已关闭的对端后, 内核回送
# ICMP 不可达, 下一次 recvfrom 会抛 ConnectionResetError(WinError 10054).
# 必须吞掉, 否则单个 agent 退出会让整个 master 监听线程崩死.
_WSAECONNRESET = 10054


class UdpListenerThread(QThread):
    """后台线程，绑定 UDP 端口接收被控端广播消息"""

    # 跨线程传递 Python 对象：emit 后不得修改对象内容
    message_received = pyqtSignal(object, str)  # (UdpMessage, remote_ip)
    # bind 失败 → emit (port, error_message); 主窗口可弹窗/状态栏告警
    bind_failed = pyqtSignal(int, str)

    def __init__(
        self,
        port: int = UDP_PORT,
        parent: QThread | None = None,
        *,
        local_tenant: str = "",
        strict_tenant: bool = True,
        master_name: str = "",
    ) -> None:
        super().__init__(parent)
        self._port = port
        self._running = False
        self._local_tenant = local_tenant
        self._strict_tenant = strict_tenant
        # 空 master_name → 退化为机器主机名 (老行为)
        self._master_name = master_name or platform.node()

    def run(self) -> None:
        self._running = True
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # P0: 1MB 接收缓冲区，防止 100+ 节点高并发心跳丢包
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        # P0: 缩短超时，提高消息响应频率
        sock.settimeout(0.5)
        try:
            try:
                sock.bind(("", self._port))
            except OSError as exc:
                # 端口被占 / 权限不足 / 防火墙拒绝: emit 告警并退出, 不让线程静默死亡
                self.bind_failed.emit(self._port, f"{exc!s}")
                self._running = False
                return
            while self._running:
                try:
                    data, addr = sock.recvfrom(_MAX_UDP_PACKET_SIZE)
                except TimeoutError:
                    continue
                except ConnectionResetError:
                    # Windows ICMP Port Unreachable, 单个对端不可达, 不影响其它节点
                    continue
                except OSError as exc:
                    if (
                        exc.errno in {errno.EMSGSIZE, _WSAEMSGSIZE, _WSAECONNRESET}
                        or getattr(exc, "winerror", None) in {_WSAEMSGSIZE, _WSAECONNRESET}
                    ):
                        continue
                    raise
                raw = data.decode("utf-8", errors="ignore").strip()
                if not raw:
                    continue
                if raw.startswith("DISCOVER_MASTER|"):
                    self._reply_discovery(sock, addr, raw)
                    continue
                msg = parse_udp_message(raw)
                if msg is not None:
                    self.message_received.emit(msg, addr[0])
        finally:
            sock.close()

    def _reply_discovery(self, sock: socket.socket, addr: tuple[str, int], raw: str) -> None:
        """租户匹配通过才回复; 否则静默 (避免机房多客户串台)."""
        parsed = parse_discover_master(raw)
        if parsed is None:
            return
        _, _, _, remote_tenant = parsed
        if not should_reply_to_discovery(
            remote_tenant,
            local_tenant=self._local_tenant,
            strict=self._strict_tenant,
        ):
            return
        response = build_udp_master_here(
            self._master_name,
            tenant_id=self._local_tenant,
        )
        sock.sendto(response.encode("utf-8"), addr)

    def stop(self) -> None:
        """请求停止并等待线程结束"""
        self._running = False
        self.wait(5000)
