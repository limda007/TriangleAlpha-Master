from datetime import datetime, timedelta

from common.models import AccountInfo, AccountStatus, NodeInfo, KamiInfo, KamiStatus, OperationRecord


class TestNodeInfo:
    def test_create_minimal(self):
        node = NodeInfo(machine_name="VM-01", ip="10.1.3.51")
        assert node.machine_name == "VM-01"
        assert node.ip == "10.1.3.51"
        assert node.status == "在线"
        assert node.group == "默认"
        assert node.elapsed == "0"

    def test_is_online_within_threshold(self):
        node = NodeInfo(machine_name="VM-01", ip="10.1.3.51")
        assert node.is_online(timeout_sec=15)

    def test_is_offline_after_timeout(self):
        node = NodeInfo(
            machine_name="VM-01", ip="10.1.3.51",
            last_seen=datetime.now() - timedelta(seconds=20),
        )
        assert not node.is_online(timeout_sec=15)

    def test_is_online_exact_boundary(self):
        """At exactly timeout boundary"""
        node = NodeInfo(
            machine_name="VM-01", ip="10.1.3.51",
            last_seen=datetime.now() - timedelta(seconds=15),
        )
        assert not node.is_online(timeout_sec=15)

    def test_default_values(self):
        node = NodeInfo(machine_name="VM-01", ip="10.1.3.51")
        assert node.cpu_percent == 0.0
        assert node.mem_percent == 0.0
        assert node.slave_version == ""
        assert node.token_key == ""
        assert node.kami_code == ""
        assert node.status_text == ""
        assert node.game_state == ""


class TestAccountInfo:
    def test_create_from_line(self):
        acc = AccountInfo.from_line("user1----pass1")
        assert acc.username == "user1"
        assert acc.password == "pass1"
        assert acc.status == AccountStatus.IDLE

    def test_masked_password(self):
        acc = AccountInfo(username="u", password="secret123")
        assert acc.masked_password == "••••••••"

    def test_from_line_full(self):
        acc = AccountInfo.from_line("user1----pass1----email@test.com----epass----备注信息")
        assert acc.username == "user1"
        assert acc.password == "pass1"
        assert acc.bind_email == "email@test.com"
        assert acc.bind_email_password == "epass"
        assert acc.notes == "备注信息"

    def test_from_line_minimal(self):
        acc = AccountInfo.from_line("user1")
        assert acc.username == "user1"
        assert acc.password == ""
        assert acc.bind_email == ""

    def test_from_line_whitespace(self):
        acc = AccountInfo.from_line("  user1  ----  pass1  ")
        assert acc.username == "user1"
        assert acc.password == "pass1"

    def test_to_line_without_notes(self):
        acc = AccountInfo(username="u", password="p", bind_email="e", bind_email_password="ep")
        assert acc.to_line() == "u----p----e----ep"

    def test_to_line_with_notes(self):
        acc = AccountInfo(username="u", password="p", bind_email="e", bind_email_password="ep", notes="n")
        assert acc.to_line() == "u----p----e----ep----n"

    def test_to_platform_line_idle(self):
        acc = AccountInfo(username="u", password="p", bind_email="e", bind_email_password="ep")
        line = acc.to_platform_line()
        parts = line.split("----")
        assert len(parts) == 10
        assert parts[0] == "u"
        assert parts[6] == "正常"
        assert parts[7] == "无"
        assert parts[8] == "无"
        assert parts[9] == "无"

    def test_to_platform_line_banned(self):
        acc = AccountInfo(username="u", password="p", bind_email="e", bind_email_password="ep",
                          status=AccountStatus.BANNED)
        line = acc.to_platform_line()
        parts = line.split("----")
        assert parts[6] == "封禁"

    def test_to_platform_line_with_dates(self):
        acc = AccountInfo(
            username="u", password="p", bind_email="e", bind_email_password="ep",
            last_login_at=datetime(2025, 1, 15, 10, 30, 0),
            completed_at=datetime(2025, 1, 15, 12, 0, 0),
        )
        line = acc.to_platform_line()
        parts = line.split("----")
        assert parts[8] == "2025-01-15 10:30:00"
        assert parts[9] == "2025-01-15 12:00:00"

    def test_masked_password_empty(self):
        acc = AccountInfo(username="u", password="")
        assert acc.masked_password == ""

    def test_all_statuses(self):
        for status in AccountStatus:
            acc = AccountInfo(username="u", status=status)
            assert acc.status == status


class TestKamiInfo:
    def test_defaults(self):
        k = KamiInfo()
        assert k.id == 0
        assert k.kami_code == ""
        assert k.status == KamiStatus.UNKNOWN
        assert k.device_used == 0
        assert k.device_total == 0
        assert k.bound_nodes == []

    def test_bound_nodes_isolation(self):
        """Each instance gets its own list (no shared mutable default)"""
        k1 = KamiInfo()
        k2 = KamiInfo()
        k1.bound_nodes.append("VM-01")
        assert k2.bound_nodes == []

    def test_all_statuses(self):
        for status in KamiStatus:
            k = KamiInfo(status=status)
            assert k.status == status

    def test_full_construction(self):
        k = KamiInfo(
            id=1, kami_code="ABC123", kami_type="online",
            end_date="2025-12-31", remaining_days=30,
            status=KamiStatus.ACTIVATED, device_used=1, device_total=3,
            activated_at="2025-01-01", created_at="2024-12-01",
            bound_nodes=["VM-01", "VM-02"],
        )
        assert k.kami_code == "ABC123"
        assert k.remaining_days == 30
        assert len(k.bound_nodes) == 2


class TestOperationRecord:
    def test_create(self):
        now = datetime.now()
        rec = OperationRecord(
            timestamp=now, op_type="START", target="VM-01",
            detail="启动脚本", result="成功",
        )
        assert rec.op_type == "START"
        assert rec.target == "VM-01"
        assert rec.detail == "启动脚本"
        assert rec.result == "成功"

    def test_defaults(self):
        now = datetime.now()
        rec = OperationRecord(timestamp=now, op_type="STOP", target="VM-02")
        assert rec.detail == ""
        assert rec.result == ""
