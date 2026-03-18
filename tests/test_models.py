from datetime import datetime, timedelta

from common.models import AccountInfo, AccountStatus, NodeInfo


class TestNodeInfo:
    def test_create_minimal(self):
        node = NodeInfo(machine_name="VM-01", ip="10.1.3.51")
        assert node.machine_name == "VM-01"
        assert node.ip == "10.1.3.51"
        assert node.status == "在线"
        assert node.group == "默认"

    def test_is_online_within_threshold(self):
        node = NodeInfo(machine_name="VM-01", ip="10.1.3.51")
        assert node.is_online(timeout_sec=15)

    def test_is_offline_after_timeout(self):
        node = NodeInfo(
            machine_name="VM-01", ip="10.1.3.51",
            last_seen=datetime.now() - timedelta(seconds=20),
        )
        assert not node.is_online(timeout_sec=15)


class TestAccountInfo:
    def test_create_from_line(self):
        acc = AccountInfo.from_line("user1----pass1")
        assert acc.username == "user1"
        assert acc.password == "pass1"
        assert acc.status == AccountStatus.IDLE

    def test_masked_password(self):
        acc = AccountInfo(username="u", password="secret123")
        assert acc.masked_password == "••••••••"
