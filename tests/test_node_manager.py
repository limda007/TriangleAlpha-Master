from datetime import datetime, timedelta

from common.protocol import UdpMessage, UdpMessageType
from master.app.core.node_manager import NodeManager


class TestNodeManager:
    def setup_method(self):
        self.nm = NodeManager()

    def test_handle_online_adds_node(self):
        msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        self.nm.handle_udp_message(msg, "10.1.3.51")
        assert "VM-01" in self.nm.nodes
        assert self.nm.nodes["VM-01"].ip == "10.1.3.51"

    def test_handle_online_updates_existing(self):
        msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        self.nm.handle_udp_message(msg, "10.1.3.51")
        self.nm.handle_udp_message(msg, "10.1.3.99")
        assert self.nm.nodes["VM-01"].ip == "10.1.3.99"

    def test_handle_offline(self):
        msg_on = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        self.nm.handle_udp_message(msg_on, "10.1.3.51")
        msg_off = UdpMessage(type=UdpMessageType.OFFLINE, machine_name="VM-01")
        self.nm.handle_udp_message(msg_off, "10.1.3.51")
        assert self.nm.nodes["VM-01"].status == "离线"

    def test_handle_status(self):
        msg_on = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        self.nm.handle_udp_message(msg_on, "10.1.3.51")
        msg_st = UdpMessage(
            type=UdpMessageType.STATUS,
            machine_name="VM-01",
            state="运行中",
            level=18,
            jin_bi="12450",
            desc="正在升级",
        )
        self.nm.handle_udp_message(msg_st, "10.1.3.51")
        assert self.nm.nodes["VM-01"].level == 18
        assert self.nm.nodes["VM-01"].jin_bi == "12450"
        assert self.nm.nodes["VM-01"].elapsed == "0"  # 默认值

    def test_handle_status_with_elapsed(self):
        msg_on = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        self.nm.handle_udp_message(msg_on, "10.1.3.51")
        msg_st = UdpMessage(
            type=UdpMessageType.STATUS,
            machine_name="VM-01",
            state="运行中",
            level=18,
            jin_bi="12450",
            desc="正在升级",
            elapsed="120",
        )
        self.nm.handle_udp_message(msg_st, "10.1.3.51")
        assert self.nm.nodes["VM-01"].elapsed == "120"

    def test_check_timeouts(self):
        msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        self.nm.handle_udp_message(msg, "10.1.3.51")
        self.nm.nodes["VM-01"].last_seen = datetime.now() - timedelta(seconds=20)
        self.nm.check_timeouts()
        assert self.nm.nodes["VM-01"].status == "离线"

    def test_online_count(self):
        for i in range(5):
            msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name=f"VM-{i:02d}", user_name="A")
            self.nm.handle_udp_message(msg, f"10.1.3.{i}")
        self.nm.nodes["VM-00"].last_seen = datetime.now() - timedelta(seconds=20)
        self.nm.check_timeouts()
        assert self.nm.online_count == 4
        assert self.nm.total_count == 5

    def test_get_nodes_by_group(self):
        msg = UdpMessage(type=UdpMessageType.EXT_ONLINE, machine_name="VM-01", user_name="A", group="A组")
        self.nm.handle_udp_message(msg, "10.1.3.51")
        msg2 = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-02", user_name="B")
        self.nm.handle_udp_message(msg2, "10.1.3.52")
        assert len(self.nm.get_nodes_by_group("A组")) == 1
        assert len(self.nm.get_nodes_by_group("默认")) == 1

    def test_ext_online_updates_kami_code(self):
        msg = UdpMessage(
            type=UdpMessageType.EXT_ONLINE,
            machine_name="VM-01",
            user_name="A",
            group="A组",
            token_key="TOKEN123",
            kami_code="KAMI456",
        )
        self.nm.handle_udp_message(msg, "10.1.3.51")
        node = self.nm.nodes["VM-01"]
        assert node.token_key == "TOKEN123"
        assert node.kami_code == "KAMI456"

    def test_node_online_emits_on_offline_to_online(self):
        """离线→在线 应重新触发 node_online 信号。"""
        emitted: list[str] = []
        self.nm.node_online.connect(lambda name: emitted.append(name))
        msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="A")
        self.nm.handle_udp_message(msg, "10.1.3.51")
        assert emitted == ["VM-01"]
        # 标记离线
        msg_off = UdpMessage(type=UdpMessageType.OFFLINE, machine_name="VM-01")
        self.nm.handle_udp_message(msg_off, "10.1.3.51")
        assert self.nm.nodes["VM-01"].status == "离线"
        # 重新上线 → 应再次发射 node_online
        self.nm.handle_udp_message(msg, "10.1.3.51")
        assert emitted == ["VM-01", "VM-01"]
        assert self.nm.nodes["VM-01"].status == "在线"

    def test_node_online_not_emitted_for_already_online(self):
        """已在线节点收到心跳时，不应重复发射 node_online。"""
        emitted: list[str] = []
        self.nm.node_online.connect(lambda name: emitted.append(name))
        msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="A")
        self.nm.handle_udp_message(msg, "10.1.3.51")
        self.nm.handle_udp_message(msg, "10.1.3.51")  # 第二次心跳
        assert emitted == ["VM-01"]  # 只触发一次

    def test_node_online_emits_on_disconnected_to_online(self):
        """断连→在线 应重新触发 node_online 信号。"""
        emitted: list[str] = []
        self.nm.node_online.connect(lambda name: emitted.append(name))
        msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="A")
        self.nm.handle_udp_message(msg, "10.1.3.51")
        assert emitted == ["VM-01"]
        # 手动标记断连（模拟超时）
        self.nm.nodes["VM-01"].status = "断连"
        # 重新上线 → 应再次发射 node_online
        self.nm.handle_udp_message(msg, "10.1.3.51")
        assert emitted == ["VM-01", "VM-01"]
        assert self.nm.nodes["VM-01"].status == "在线"
