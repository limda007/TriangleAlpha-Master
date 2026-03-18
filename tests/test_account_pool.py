from common.models import AccountStatus
from master.app.core.account_pool import AccountPool


class TestAccountPool:
    def test_load_from_text(self):
        pool = AccountPool()
        pool.load_from_text("user1----pass1\nuser2----pass2\n\nuser3----pass3")
        assert pool.total_count == 3

    def test_allocate(self):
        pool = AccountPool()
        pool.load_from_text("u1----p1\nu2----p2")
        acc = pool.allocate("VM-01")
        assert acc is not None
        assert acc.username == "u1"
        assert acc.status == AccountStatus.IN_USE
        assert acc.assigned_machine == "VM-01"
        assert pool.available_count == 1

    def test_allocate_skips_used(self):
        pool = AccountPool()
        pool.load_from_text("u1----p1\nu2----p2")
        pool.allocate("VM-01")
        acc = pool.allocate("VM-02")
        assert acc is not None
        assert acc.username == "u2"

    def test_allocate_returns_none_when_empty(self):
        pool = AccountPool()
        pool.load_from_text("u1----p1")
        pool.allocate("VM-01")
        assert pool.allocate("VM-02") is None

    def test_complete(self):
        pool = AccountPool()
        pool.load_from_text("u1----p1")
        pool.allocate("VM-01")
        pool.complete("VM-01", level=18)
        assert pool.accounts[0].status == AccountStatus.COMPLETED
        assert pool.accounts[0].level == 18
        assert pool.completed_count == 1

    def test_release(self):
        pool = AccountPool()
        pool.load_from_text("u1----p1")
        pool.allocate("VM-01")
        pool.release("VM-01")
        assert pool.available_count == 1
