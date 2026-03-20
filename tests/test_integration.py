"""集成测试：组件间协作"""

from datetime import datetime, timedelta

from common.protocol import (
    TcpCommand,
    UdpMessage,
    UdpMessageType,
    build_tcp_command,
    build_udp_ext_online,
    build_udp_online,
    build_udp_status,
    parse_udp_message,
)
from master.app.core.account_db import AccountDB
from master.app.core.node_manager import NodeManager


class TestNodeManagerAccountDBIntegration:
    """NodeManager + AccountDB 协作"""

    def test_account_allocation_for_requesting_node(self, tmp_path):
        nm = NodeManager()
        pool = AccountDB(tmp_path / "test.db")
        pool.import_fresh("user1----pass1\nuser2----pass2\nuser3----pass3")

        # 模拟 3 个节点上线
        for i in range(3):
            msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name=f"VM-{i:02d}", user_name="Admin")
            nm.handle_udp_message(msg, f"10.1.3.{i}")

        # 给每个节点分配账号
        for name in nm.nodes:
            acc = pool.allocate(name)
            assert acc is not None

        assert pool.available_count == 0
        assert pool.in_use_count == 3

        # 完成一个
        pool.complete("VM-00", level=18)
        assert pool.completed_count == 1
        assert pool.in_use_count == 2
        pool.close()

    def test_node_timeout_releases_do_not_auto_release_accounts(self, tmp_path):
        """节点离线后账号不会自动释放"""
        nm = NodeManager()
        pool = AccountDB(tmp_path / "test.db")
        pool.import_fresh("user1----pass1")

        msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        nm.handle_udp_message(msg, "10.1.3.1")
        pool.allocate("VM-01")

        # 强制超时
        nm.nodes["VM-01"].last_seen = datetime.now() - timedelta(seconds=20)
        nm.check_timeouts()

        assert nm.nodes["VM-01"].status == "离线"
        # 账号仍然 IN_USE（不会自动释放）
        assert pool.in_use_count == 1
        pool.close()


class TestProtocolRoundTrip:
    """协议编码 -> 解码往返测试"""

    def test_online_roundtrip(self):
        raw = build_udp_online("VM-01", "Admin")
        msg = parse_udp_message(raw)
        assert msg.type == UdpMessageType.ONLINE
        assert msg.machine_name == "VM-01"
        assert msg.user_name == "Admin"

    def test_ext_online_roundtrip(self):
        raw = build_udp_ext_online("VM-01", "Admin", 55.5, 70.2, "2.0.0", "B组")
        msg = parse_udp_message(raw)
        assert msg.type == UdpMessageType.EXT_ONLINE
        assert msg.cpu_percent == 55.5
        assert msg.mem_percent == 70.2
        assert msg.slave_version == "2.0.0"
        assert msg.group == "B组"

    def test_status_roundtrip(self):
        raw = build_udp_status("VM-01", "升级中", 18, "12450", "正在升级")
        msg = parse_udp_message(raw)
        assert msg.type == UdpMessageType.STATUS
        assert msg.level == 18
        assert msg.jin_bi == "12450"
        assert msg.elapsed == "0"  # 默认 elapsed

    def test_status_roundtrip_with_elapsed(self):
        raw = build_udp_status("VM-01", "升级中", 18, "12450", "正在升级", "360")
        msg = parse_udp_message(raw)
        assert msg.type == UdpMessageType.STATUS
        assert msg.level == 18
        assert msg.elapsed == "360"

    def test_tcp_update_txt_roundtrip(self):
        import base64

        original = "user1----pass1\nuser2----pass2"
        cmd = build_tcp_command(TcpCommand.UPDATE_TXT, payload=original)
        # 模拟 slave 端解码
        payload = cmd.split("|", 1)[1]
        decoded = base64.b64decode(payload).decode("utf-8")
        assert decoded == original


class TestNodeManagerMultiMessage:
    """NodeManager 多消息序列测试"""

    def test_online_then_status_updates_fields(self):
        nm = NodeManager()
        nm.handle_udp_message(
            UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin"),
            "10.1.3.1",
        )
        nm.handle_udp_message(
            UdpMessage(
                type=UdpMessageType.STATUS,
                machine_name="VM-01",
                state="运行中",
                level=15,
                jin_bi="8000",
                desc="正常运行",
                elapsed="45",
            ),
            "10.1.3.1",
        )
        node = nm.nodes["VM-01"]
        assert node.level == 15
        assert node.jin_bi == "8000"
        assert node.elapsed == "45"

    def test_ext_online_updates_system_info(self):
        nm = NodeManager()
        nm.handle_udp_message(
            UdpMessage(
                type=UdpMessageType.EXT_ONLINE,
                machine_name="VM-01",
                user_name="Admin",
                cpu_percent=45.0,
                mem_percent=60.0,
                slave_version="2.0.0",
                group="A组",
            ),
            "10.1.3.1",
        )
        node = nm.nodes["VM-01"]
        assert node.cpu_percent == 45.0
        assert node.mem_percent == 60.0
        assert node.slave_version == "2.0.0"
        assert node.group == "A组"

    def test_offline_then_online_recovers(self):
        nm = NodeManager()
        nm.handle_udp_message(
            UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin"),
            "10.1.3.1",
        )
        nm.handle_udp_message(
            UdpMessage(type=UdpMessageType.OFFLINE, machine_name="VM-01"),
            "10.1.3.1",
        )
        assert nm.nodes["VM-01"].status == "离线"

        nm.handle_udp_message(
            UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin"),
            "10.1.3.1",
        )
        assert nm.nodes["VM-01"].status == "在线"

    def test_50_nodes_performance(self):
        """60 个节点应能正常处理"""
        nm = NodeManager()
        for i in range(60):
            nm.handle_udp_message(
                UdpMessage(
                    type=UdpMessageType.EXT_ONLINE,
                    machine_name=f"VM-{i:03d}",
                    user_name="Admin",
                    cpu_percent=50.0,
                    mem_percent=50.0,
                    slave_version="2.0.0",
                    group=f"G{i % 5}",
                ),
                f"10.1.{i // 256}.{i % 256}",
            )
        assert nm.total_count == 60
        assert nm.online_count == 60
        assert len(nm.groups) == 5

    def test_history_records_operations(self):
        nm = NodeManager()
        nm.add_history("STARTEXE", "VM-01", "启动脚本", "成功")
        nm.add_history("STOPEXE", "VM-02", "停止脚本", "成功")
        assert len(nm.history) == 2
        assert nm.history[0].op_type == "STARTEXE"
        assert nm.history[1].target == "VM-02"
