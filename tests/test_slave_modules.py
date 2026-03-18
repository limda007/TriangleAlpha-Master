"""Slave 模块单元测试：heartbeat、process_manager"""

import platform

from common.protocol import UdpMessageType, build_udp_ext_online, parse_udp_message
from slave.heartbeat import HeartbeatService
from slave.process_manager import ProcessManager


class TestHeartbeatService:
    def test_default_machine_name(self):
        svc = HeartbeatService()
        assert svc.machine_name == platform.node()

    def test_set_group(self):
        svc = HeartbeatService()
        svc.set_group("A组")
        assert svc._group == "A组"

    def test_builds_valid_ext_online_message(self):
        msg_str = build_udp_ext_online("VM-01", "Admin", 45.2, 60.1, "2.0.0", "A组")
        parsed = parse_udp_message(msg_str)
        assert parsed is not None
        assert parsed.type == UdpMessageType.EXT_ONLINE
        assert parsed.machine_name == "VM-01"
        assert parsed.cpu_percent == 45.2
        assert parsed.group == "A组"

    def test_custom_master_ip_and_port(self):
        svc = HeartbeatService(master_ip="192.168.1.100", port=9999, interval=5)
        assert svc._master_ip == "192.168.1.100"
        assert svc._port == 9999
        assert svc._interval == 5

    def test_stop_sets_running_false(self):
        svc = HeartbeatService()
        svc._running = True
        svc.stop()
        assert svc._running is False


class TestProcessManager:
    def test_init_with_base_dir(self, tmp_path):
        pm = ProcessManager(str(tmp_path))
        assert pm._base_dir == tmp_path
