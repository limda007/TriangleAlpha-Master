"""UdpListenerThread Windows ICMP Port Unreachable 容错的 TDD 单元测试.

Bug:
    Windows 平台下, master sendto 给已关闭/不可达的对端后, 内核会通过 ICMP
    Port Unreachable 通知本地 socket; 下一次 recvfrom 抛 ConnectionResetError
    (WinError 10054 / WSAECONNRESET). 现有 OSError 分支白名单仅过滤 EMSGSIZE,
    导致线程崩溃, master 端 GUI 节点列表全部消失.

Fix:
    - 在 recvfrom 的 OSError 分支增加 WSAECONNRESET 过滤, 视为可恢复, continue
    - 不让 master 端 UDP 监听线程因为单个 agent 退出而静默死亡

测试模式参考 tests/test_master_fixes.py::TestC4UdpListenerOversizeDatagram.
"""
from __future__ import annotations

from unittest.mock import patch


class TestUdpListenerConnReset:
    """ICMP Port Unreachable (WinError 10054) 必须被吞掉, 线程继续 recv."""

    def test_connection_reset_does_not_crash_thread(self) -> None:
        from master.app.core.udp_listener import UdpListenerThread

        recv_calls: list[int] = []

        class FakeSocket:
            def __init__(self) -> None:
                self._closed = False

            def setsockopt(self, *_args) -> None:
                return None

            def settimeout(self, _t: float) -> None:
                return None

            def bind(self, _addr: tuple[str, int]) -> None:
                return None

            def recvfrom(self, _bufsize: int):  # type: ignore[no-untyped-def]
                recv_calls.append(1)
                if len(recv_calls) == 1:
                    # 模拟 Windows ICMP Port Unreachable
                    err = ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。")
                    err.winerror = 10054  # type: ignore[attr-defined]
                    raise err
                if len(recv_calls) == 2:
                    return (b"ONLINE|machineX|userX", ("192.168.1.10", 12345))
                # 之后让线程退出
                listener._running = False  # type: ignore[name-defined]  # noqa: F821
                raise TimeoutError

            def close(self) -> None:
                self._closed = True

            def sendto(self, *_args, **_kwargs) -> int:
                return 0

        listener = UdpListenerThread(port=8888)

        captured: list[tuple[object, str]] = []
        listener.message_received.connect(lambda msg, ip: captured.append((msg, ip)))

        with patch("master.app.core.udp_listener.socket.socket", return_value=FakeSocket()):
            listener.run()

        # 至少调用 recvfrom 2 次 (说明第一次 ConnectionResetError 没把线程崩死)
        assert len(recv_calls) >= 2, f"线程在 ConnectionResetError 后没有继续 recv: {recv_calls}"
        # 且第二次的合法消息应该被 emit
        assert len(captured) == 1
        msg, ip = captured[0]
        assert ip == "192.168.1.10"

    def test_connection_reset_via_oserror_winerror_also_handled(self) -> None:
        """有些 Python 版本会把 10054 包成 OSError 而非 ConnectionResetError."""
        from master.app.core.udp_listener import UdpListenerThread

        recv_calls: list[int] = []

        class FakeSocket:
            def setsockopt(self, *_args) -> None:
                return None

            def settimeout(self, _t: float) -> None:
                return None

            def bind(self, _addr: tuple[str, int]) -> None:
                return None

            def recvfrom(self, _bufsize: int):  # type: ignore[no-untyped-def]
                recv_calls.append(1)
                if len(recv_calls) == 1:
                    err = OSError(10054, "WSAECONNRESET")
                    err.winerror = 10054  # type: ignore[attr-defined]
                    raise err
                listener._running = False
                raise TimeoutError

            def close(self) -> None:
                return None

            def sendto(self, *_args, **_kwargs) -> int:
                return 0

        listener = UdpListenerThread(port=8888)

        with patch("master.app.core.udp_listener.socket.socket", return_value=FakeSocket()):
            listener.run()

        assert len(recv_calls) >= 2
