"""UdpListenerThread bind 失败告警的 TDD 单元测试.

模式参考 tests/test_master_fixes.py::TestC4UdpListenerOversizeDatagram.
"""
from __future__ import annotations

from unittest.mock import patch


class TestUdpListenerBindFailure:
    """bind 失败必须 emit ``bind_failed(port, msg)``, 而不是静默崩溃线程."""

    def test_bind_failure_emits_signal_with_port_and_message(self) -> None:
        from master.app.core.udp_listener import UdpListenerThread

        captured: list[tuple[int, str]] = []

        class FakeSocket:
            def setsockopt(self, *_args) -> None:
                return None

            def settimeout(self, _t: float) -> None:
                return None

            def bind(self, _addr: tuple[str, int]) -> None:
                raise OSError(10048, "Only one usage of each socket address (...)")

            def close(self) -> None:
                return None

        listener = UdpListenerThread(port=8888)
        listener.bind_failed.connect(lambda port, msg: captured.append((port, msg)))

        with patch("master.app.core.udp_listener.socket.socket", return_value=FakeSocket()):
            listener.run()

        assert len(captured) == 1
        port, msg = captured[0]
        assert port == 8888
        assert "Only one usage" in msg or "10048" in msg

    def test_bind_failure_does_not_emit_message_received(self) -> None:
        from master.app.core.udp_listener import UdpListenerThread

        msgs: list[object] = []

        class FakeSocket:
            def setsockopt(self, *_args) -> None:
                return None

            def settimeout(self, _t: float) -> None:
                return None

            def bind(self, _addr: tuple[str, int]) -> None:
                raise OSError("boom")

            def close(self) -> None:
                return None

            def recvfrom(self, _n: int) -> tuple[bytes, tuple[str, int]]:
                msgs.append("RECV_CALLED")  # pragma: no cover - 不应被调
                raise AssertionError("recvfrom must not be reached after bind failure")

        listener = UdpListenerThread(port=8888)
        listener.message_received.connect(lambda m, ip: msgs.append((m, ip)))

        with patch("master.app.core.udp_listener.socket.socket", return_value=FakeSocket()):
            listener.run()

        assert msgs == []

    def test_bind_failure_marks_running_false(self) -> None:
        from master.app.core.udp_listener import UdpListenerThread

        class FakeSocket:
            def setsockopt(self, *_args) -> None:
                return None

            def settimeout(self, _t: float) -> None:
                return None

            def bind(self, _addr: tuple[str, int]) -> None:
                raise OSError("boom")

            def close(self) -> None:
                return None

        listener = UdpListenerThread(port=8888)
        with patch("master.app.core.udp_listener.socket.socket", return_value=FakeSocket()):
            listener.run()
        assert listener._running is False  # noqa: SLF001 - 验证内部状态收尾

    def test_bind_failure_closes_socket(self) -> None:
        from master.app.core.udp_listener import UdpListenerThread

        closed: list[bool] = []

        class FakeSocket:
            def setsockopt(self, *_args) -> None:
                return None

            def settimeout(self, _t: float) -> None:
                return None

            def bind(self, _addr: tuple[str, int]) -> None:
                raise OSError("boom")

            def close(self) -> None:
                closed.append(True)

        listener = UdpListenerThread(port=8888)
        with patch("master.app.core.udp_listener.socket.socket", return_value=FakeSocket()):
            listener.run()
        assert closed == [True]
