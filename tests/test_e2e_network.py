"""端到端网络测试：实际 UDP/TCP 通信"""

import asyncio
import base64
import contextlib
import socket
import threading
import time

from common.protocol import (
    TcpCommand,
    build_tcp_command,
    build_udp_ext_online,
    parse_udp_message,
)


class TestUdpCommunication:
    """UDP 实际收发测试"""

    def test_udp_send_receive(self):
        """模拟 slave 发送心跳、master 接收"""
        port = 18888

        received = []

        def listener():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(5.0)
            sock.bind(("127.0.0.1", port))
            try:
                data, _addr = sock.recvfrom(4096)
                received.append(data.decode("utf-8"))
            except TimeoutError:
                pass
            finally:
                sock.close()

        t = threading.Thread(target=listener)
        t.start()
        time.sleep(0.1)

        # 发送心跳
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        msg = build_udp_ext_online("TEST-VM", "Admin", 50.0, 60.0, "2.0.0", "默认")
        sock.sendto(msg.encode("utf-8"), ("127.0.0.1", port))
        sock.close()

        t.join(timeout=5)

        assert len(received) == 1
        parsed = parse_udp_message(received[0])
        assert parsed is not None
        assert parsed.machine_name == "TEST-VM"
        assert parsed.cpu_percent == 50.0


class TestTcpCommunication:
    """TCP 实际收发测试"""

    def test_tcp_command_send_receive(self):
        """模拟 master 发送指令、slave 接收"""
        port = 19999

        received = []

        def server():
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.settimeout(5.0)
            srv.bind(("127.0.0.1", port))
            srv.listen(1)
            try:
                conn, _ = srv.accept()
                data = conn.recv(4096)
                received.append(data.decode("utf-8").strip())
                conn.close()
            except TimeoutError:
                pass
            finally:
                srv.close()

        t = threading.Thread(target=server)
        t.start()
        time.sleep(0.1)

        # 发送指令
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(("127.0.0.1", port))
        cmd = build_tcp_command(TcpCommand.START_EXE)
        sock.sendall((cmd + "\n").encode("utf-8"))
        sock.close()

        t.join(timeout=5)

        assert len(received) == 1
        assert received[0] == "STARTEXE|"

    def test_tcp_update_key_roundtrip(self):
        """完整往返: 构建 key 指令 -> TCP 发送 -> 另一端解码"""
        port = 19998
        key = "ZKDD38B024H2460444BFA36A3C154561"

        received_key = []

        def server():
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.settimeout(5.0)
            srv.bind(("127.0.0.1", port))
            srv.listen(1)
            try:
                conn, _ = srv.accept()
                data = conn.recv(4096).decode("utf-8").strip()
                if data.startswith("UPDATEKEY|"):
                    payload = data[len("UPDATEKEY|"):]
                    decoded = base64.b64decode(payload).decode("utf-8")
                    received_key.append(decoded)
                conn.close()
            except TimeoutError:
                pass
            finally:
                srv.close()

        t = threading.Thread(target=server)
        t.start()
        time.sleep(0.1)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(("127.0.0.1", port))
        cmd = build_tcp_command(TcpCommand.UPDATE_KEY, payload=key)
        sock.sendall((cmd + "\n").encode("utf-8"))
        sock.close()

        t.join(timeout=5)

        assert len(received_key) == 1
        assert received_key[0] == key


class TestSlaveCommandHandler:
    """Slave CommandHandler 端到端测试"""

    def test_slave_receives_and_processes_update_txt(self, tmp_path):
        """启动 slave 命令处理器，发送 UPDATETXT，验证文件写入"""
        port = 19997
        accounts_text = "user1----pass1\nuser2----pass2"

        async def run_test():
            from slave.command_handler import CommandHandler

            handler = CommandHandler(str(tmp_path), port=port)
            server_task = asyncio.create_task(handler.run())
            await asyncio.sleep(0.2)

            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            cmd = build_tcp_command(TcpCommand.UPDATE_TXT, payload=accounts_text)
            writer.write((cmd + "\n").encode("utf-8"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()

            await asyncio.sleep(0.3)

            accounts_file = tmp_path / "accounts.txt"
            assert accounts_file.exists()
            assert accounts_file.read_text(encoding="utf-8") == accounts_text

            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task

        asyncio.run(run_test())

    def test_slave_receives_update_key(self, tmp_path):
        """发送 UPDATEKEY，验证 key.txt 写入"""
        port = 19996
        key = "TESTKEY123"

        async def run_test():
            from slave.command_handler import CommandHandler

            handler = CommandHandler(str(tmp_path), port=port)
            server_task = asyncio.create_task(handler.run())
            await asyncio.sleep(0.2)

            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            cmd = build_tcp_command(TcpCommand.UPDATE_KEY, payload=key)
            writer.write((cmd + "\n").encode("utf-8"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.3)

            key_file = tmp_path / "key.txt"
            assert key_file.exists()
            assert key_file.read_text(encoding="utf-8") == key

            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task

        asyncio.run(run_test())

    def test_slave_receives_delete_file(self, tmp_path):
        """发送 DELETEFILE，验证文件被删除"""
        port = 19995
        # 创建待删除文件
        test_file = tmp_path / "old_data.txt"
        test_file.write_text("delete me")

        async def run_test():
            from slave.command_handler import CommandHandler

            handler = CommandHandler(str(tmp_path), port=port)
            server_task = asyncio.create_task(handler.run())
            await asyncio.sleep(0.2)

            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            cmd = build_tcp_command(TcpCommand.DELETE_FILE, payload="old_data.txt")
            writer.write((cmd + "\n").encode("utf-8"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.3)

            assert not test_file.exists()

            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task

        asyncio.run(run_test())
