"""AccountDB 单元测试"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Generator
from pathlib import Path

import pytest

from common.models import AccountStatus
from master.app.core.account_db import AccountDB

_SAMPLE = "u1----p1----e1----ep1----note1\nu2----p2\nu3----p3----e3----ep3"


@pytest.fixture()
def db(tmp_path: Path) -> Generator[AccountDB, None, None]:
    """每个测试一个干净的 DB"""
    d = AccountDB(tmp_path / "test.db")
    yield d
    d.close()


class TestImport:
    def test_import_fresh(self, db: AccountDB) -> None:
        db.import_fresh(_SAMPLE)
        assert db.total_count == 3
        assert db.available_count == 3

    def test_import_fresh_clears_old(self, db: AccountDB) -> None:
        db.import_fresh("old----pass")
        db.import_fresh("new----pass")
        assert db.total_count == 1
        accs = db.get_all_accounts()
        assert accs[0].username == "new"

    def test_load_from_text_merge(self, db: AccountDB) -> None:
        """load_from_text 使用 INSERT OR IGNORE，不覆盖已有账号"""
        db.import_fresh("u1----p1\nu2----p2")
        db.load_from_text("u2----new_pass\nu3----p3")
        assert db.total_count == 3
        # u2 密码不应被覆盖
        accs = {a.username: a for a in db.get_all_accounts()}
        assert accs["u2"].password == "p2"  # 保持原始密码
        assert accs["u3"].password == "p3"

    def test_load_from_file(self, db: AccountDB, tmp_path: Path) -> None:
        f = tmp_path / "accounts.txt"
        f.write_text("a1----p1\na2----p2", encoding="utf-8")
        inserted, skipped = db.load_from_file(f)
        assert inserted == 2
        assert skipped == 0
        assert db.total_count == 2

    def test_load_from_file_not_found(self, db: AccountDB) -> None:
        with pytest.raises(OSError, match="无法读取"):
            db.load_from_file("/nonexistent/path.txt")

    def test_get_config_returns_default_after_close(self, tmp_path: Path) -> None:
        db = AccountDB(tmp_path / "closed.db")
        db.set_config("api_key", "secret")

        db.close()

        assert db.get_config("api_key", "fallback") == "fallback"

    def test_empty_lines_ignored(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1\n\n  \nu2----p2\n")
        assert db.total_count == 2

    def test_five_fields(self, db: AccountDB) -> None:
        db.import_fresh("user----pass----mail----mailpw----备注")
        acc = db.get_all_accounts()[0]
        assert acc.username == "user"
        assert acc.bind_email == "mail"
        assert acc.bind_email_password == "mailpw"
        assert acc.notes == "备注"


class TestAllocate:
    def test_allocate_basic(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1\nu2----p2")
        acc = db.allocate("VM-01")
        assert acc is not None
        assert acc.username == "u1"
        assert acc.status == AccountStatus.IN_USE
        assert acc.assigned_machine == "VM-01"
        assert db.available_count == 1
        assert db.in_use_count == 1

    def test_allocate_sequential(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1\nu2----p2")
        db.allocate("VM-01")
        acc = db.allocate("VM-02")
        assert acc is not None
        assert acc.username == "u2"

    def test_allocate_empty(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        assert db.allocate("VM-02") is None

    def test_allocate_no_accounts(self, db: AccountDB) -> None:
        assert db.allocate("VM-01") is None

    def test_allocate_same_machine_returns_existing_account(self, db: AccountDB) -> None:
        """同一机器重复申请时应返回已绑定账号，而不是继续吃掉新账号。"""
        db.import_fresh("u1----p1\nu2----p2")

        first = db.allocate("VM-01")
        second = db.allocate("VM-01")

        assert first is not None
        assert second is not None
        assert first.username == "u1"
        assert second.username == "u1"
        assert db.available_count == 1
        assert db.in_use_count == 1


class TestComplete:
    def test_complete_basic(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        db.complete("VM-01", level=18)
        accs = db.get_all_accounts()
        assert accs[0].status == AccountStatus.COMPLETED
        assert accs[0].level == 18
        assert accs[0].completed_at is not None
        assert db.completed_count == 1

    def test_complete_nonexistent_machine(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        db.complete("VM-NONE")  # 不报错，静默忽略
        assert db.completed_count == 0

    def test_update_from_status_auto_binds_by_username(self, db: AccountDB) -> None:
        """未经 allocate 的账号，STATUS 上报时按 username 自动绑定"""
        db.import_fresh("u1----p1")
        assert db.get_all_accounts()[0].status == AccountStatus.IDLE
        # slave 上报 STATUS，传入 current_account
        db.update_from_status("VM-01", 10, "5000", "运行中", current_account="u1")
        acc = db.get_all_accounts()[0]
        assert acc.status == AccountStatus.IN_USE
        assert acc.assigned_machine == "VM-01"
        assert acc.level == 10
        assert acc.jin_bi == "5000"

    def test_update_from_status_auto_bind_then_complete(self, db: AccountDB) -> None:
        """自动绑定的账号也能正常流转到已完成"""
        db.import_fresh("u1----p1")
        db.update_from_status("VM-01", 18, "50000", "运行中", current_account="u1")
        db.update_from_status("VM-01", 18, "50000", "已完成", current_account="u1")
        acc = db.get_all_accounts()[0]
        assert acc.status == AccountStatus.COMPLETED
        assert acc.completed_at is not None

    def test_update_from_status_auto_bind_does_not_steal_running_account(self, db: AccountDB) -> None:
        """自动绑定不能把别的机器正在跑的账号改绑走。"""
        db.import_fresh("u1----p1")
        db.allocate("VM-01")

        db.update_from_status("VM-02", 10, "5000", "运行中", current_account="u1")

        acc_vm1 = db.get_account_for_machine("VM-01")
        acc_vm2 = db.get_account_for_machine("VM-02")
        assert acc_vm1 is not None
        assert acc_vm1.username == "u1"
        assert acc_vm2 is None


    def test_update_from_status_zero_level_does_not_overwrite(self, db: AccountDB) -> None:
        """level=0 不应覆盖已有的非零等级（MAX 防护）"""
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        db.update_from_status("VM-01", 15, "5000", "运行中")
        # 模拟 IPC 超时后发送零值
        db.update_from_status("VM-01", 0, "5000", "运行中")
        acc = db.get_all_accounts()[0]
        assert acc.level == 15  # 保持原值，不被零覆盖

    def test_update_from_status_zero_jinbi_does_not_overwrite(self, db: AccountDB) -> None:
        """jin_bi='0' 不应覆盖已有的非零金币"""
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        db.update_from_status("VM-01", 15, "5000", "运行中")
        # 模拟脚本停止后零值
        db.update_from_status("VM-01", 18, "0", "运行中")
        acc = db.get_all_accounts()[0]
        assert acc.jin_bi == "5000"  # 保持原值
        assert acc.level == 18  # level 可以增长

    def test_update_from_status_level_only_increases(self, db: AccountDB) -> None:
        """等级只增不减（MAX 语义）"""
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        db.update_from_status("VM-01", 18, "50000", "运行中")
        db.update_from_status("VM-01", 5, "60000", "运行中")
        acc = db.get_all_accounts()[0]
        assert acc.level == 18  # MAX(18, 5) = 18
        assert acc.jin_bi == "60000"  # jin_bi 正常更新

    def test_update_from_status_auto_bind_zero_protection(self, db: AccountDB) -> None:
        """自动绑定路径也应有零值保护"""
        db.import_fresh("u1----p1")
        db.update_from_status("VM-01", 10, "3000", "运行中", current_account="u1")
        # 后续零值不覆盖
        db.update_from_status("VM-01", 0, "0", "运行中", current_account="u1")
        acc = db.get_all_accounts()[0]
        assert acc.level == 10
        assert acc.jin_bi == "3000"


class TestRelease:
    def test_release_basic(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        db.release("VM-01")
        assert db.available_count == 1
        assert db.in_use_count == 0
        acc = db.get_all_accounts()[0]
        assert acc.assigned_machine == ""

    def test_release_blocks_same_account_status_from_rebinding(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        db.allocate("VM-01")

        db.release("VM-01")
        db.update_from_status("VM-01", 10, "5000", "运行中", current_account="u1")

        assert db.get_account_for_machine("VM-01") is None
        acc = db.get_all_accounts()[0]
        assert acc.status == AccountStatus.IDLE
        assert acc.assigned_machine == ""

    def test_release_block_does_not_affect_other_machine(self, db: AccountDB) -> None:
        """release VM-01 不应阻止同账号被 VM-02 通过 STATUS 自动绑定 (按 (machine, username) 隔离)."""
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        db.release("VM-01")

        # 另一台机器跑同一账号 (例如手动迁移) → 应允许自动绑定
        db.update_from_status("VM-02", 10, "5000", "运行中", current_account="u1")

        bound = db.get_account_for_machine("VM-02")
        assert bound is not None
        assert bound.username == "u1"

    def test_reallocate_clears_release_block(self, db: AccountDB) -> None:
        """release 后再次 allocate 同账号到同机器 → block 应被清除, STATUS 可正常绑定."""
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        db.release("VM-01")
        # 重新分配 → 由于池里只有一个号, 应再次分到 u1
        again = db.allocate("VM-01")
        assert again is not None and again.username == "u1"
        # 此时 STATUS 应能正常更新, 不被 block 拦截
        db.update_from_status("VM-01", 20, "9000", "运行中", current_account="u1")
        bound = db.get_account_for_machine("VM-01")
        assert bound is not None and bound.level == 20

    def test_release_block_is_bounded_under_churn(self, db: AccountDB) -> None:
        """长跑场景: release-allocate 循环远超上界, 集合不应无限增长."""
        from master.app.core.account_db import _RELEASE_BLOCK_MAX_ENTRIES

        # 制造略超上界的不同 (machine, username)
        churn = _RELEASE_BLOCK_MAX_ENTRIES + 50
        for i in range(churn):
            db._release_block_add(f"VM-{i:05d}", f"u{i}")  # noqa: SLF001
        assert len(db._released_account_blocks) <= _RELEASE_BLOCK_MAX_ENTRIES  # noqa: SLF001
        # 最旧条目已被淘汰
        assert ("VM-00000", "u0") not in db._released_account_blocks  # noqa: SLF001
        # 最新条目仍在
        assert (f"VM-{churn - 1:05d}", f"u{churn - 1}") in db._released_account_blocks  # noqa: SLF001

    def test_release_block_reinsert_refreshes_position(self, db: AccountDB) -> None:
        """重复 release 同 (machine, username) 不重复占位且刷新 FIFO 顺序."""
        db._release_block_add("VM-01", "u1")  # noqa: SLF001
        db._release_block_add("VM-02", "u2")  # noqa: SLF001
        db._release_block_add("VM-01", "u1")  # noqa: SLF001 — 重新插入
        keys = list(db._released_account_blocks.keys())  # noqa: SLF001
        assert keys == [("VM-02", "u2"), ("VM-01", "u1")]

    def test_release_nonexistent(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        db.release("VM-NONE")  # 静默忽略
        assert db.available_count == 1


class TestQuery:
    def test_get_account_for_machine(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1\nu2----p2")
        db.allocate("VM-01")
        acc = db.get_account_for_machine("VM-01")
        assert acc is not None
        assert acc.username == "u1"

    def test_get_account_for_machine_none(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        assert db.get_account_for_machine("VM-01") is None

    def test_get_all_accounts_ordered(self, db: AccountDB) -> None:
        db.import_fresh("b----p\na----p\nc----p")
        accs = db.get_all_accounts()
        # 按插入顺序 (id)
        assert [a.username for a in accs] == ["b", "a", "c"]


class TestExport:
    def test_export_completed(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1----e1----ep1\nu2----p2")
        db.allocate("VM-01")
        db.complete("VM-01", level=20)
        db._conn.execute(
            "UPDATE accounts SET last_login_at='2026-03-24 09:00:00' WHERE username='u1'"
        )
        db._conn.commit()
        text = db.export_completed()
        lines = text.splitlines()
        assert lines[0].startswith("账号----密码")  # 表头
        assert "u1----p1----e1----ep1----20----0----正常----无----2026-03-24 09:00:00----" in text
        # u2 未完成，不在导出中
        assert "u2" not in text

    def test_export_empty(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        text = db.export_completed()
        lines = text.splitlines()
        assert len(lines) == 1  # 仅表头
        assert lines[0].startswith("账号----密码")

    def test_export_all(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1\nu2----p2")
        db.allocate("VM-01")
        db.complete("VM-01", level=20)
        db._conn.execute(
            "UPDATE accounts SET last_login_at='2026-03-24 09:00:00' WHERE username='u1'"
        )
        db._conn.commit()
        text = db.export_all()
        lines = text.splitlines()
        assert lines[0].startswith("账号----密码")  # 表头
        assert len(lines) == 3  # 表头 + 2 账号
        assert "u1" in text
        assert "u2" in text
        assert "已完成" in text
        assert "空闲中" in text
        assert "2026-03-24 09:00:00" in text


class TestClear:
    def test_clear_all(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1\nu2----p2")
        db.clear_all()
        assert db.total_count == 0
        assert db.get_all_accounts() == []


class TestPersistence:
    def test_reopen_preserves_data(self, tmp_path: Path) -> None:
        """关闭后重新打开，数据仍在"""
        db_path = tmp_path / "persist.db"
        db1 = AccountDB(db_path)
        db1.import_fresh("u1----p1\nu2----p2")
        db1.allocate("VM-01")
        db1.close()

        db2 = AccountDB(db_path)
        assert db2.total_count == 2
        assert db2.in_use_count == 1
        acc = db2.get_account_for_machine("VM-01")
        assert acc is not None
        assert acc.username == "u1"
        db2.close()

    def test_reopen_preserves_completed(self, tmp_path: Path) -> None:
        db_path = tmp_path / "persist2.db"
        db1 = AccountDB(db_path)
        db1.import_fresh("u1----p1")
        db1.allocate("VM-01")
        db1.complete("VM-01", level=25)
        db1.close()

        db2 = AccountDB(db_path)
        assert db2.completed_count == 1
        accs = db2.get_all_accounts()
        assert accs[0].level == 25
        assert accs[0].completed_at is not None
        db2.close()

    def test_reopen_allocate_same_machine_returns_existing_account(self, tmp_path: Path) -> None:
        """不同连接访问同一 DB 时，同一机器重复取号也不能拿到第二个账号。"""
        db_path = tmp_path / "persist3.db"
        db1 = AccountDB(db_path)
        db1.import_fresh("u1----p1\nu2----p2")
        db2 = AccountDB(db_path)
        try:
            first = db1.allocate("VM-01")
            second = db2.allocate("VM-01")

            assert first is not None
            assert second is not None
            assert first.username == "u1"
            assert second.username == "u1"
            assert db2.get_account_for_machine("VM-01") is not None
            assert db2.get_account_for_machine("VM-02") is None
            assert len([acc for acc in db2.get_all_accounts() if acc.assigned_machine == "VM-01"]) == 1
        finally:
            db2.close()
            db1.close()

    def test_concurrent_allocate_same_machine_does_not_consume_second_account(self, tmp_path: Path) -> None:
        """并发取号时，同一机器也只能绑定一个账号。"""
        db_path = tmp_path / "persist4.db"
        setup = AccountDB(db_path)
        setup.import_fresh("u1----p1\nu2----p2")
        setup.close()

        barrier = threading.Barrier(2)
        results: list[str | None] = []
        errors: list[BaseException] = []

        def worker() -> None:
            local_db = AccountDB(db_path)
            try:
                barrier.wait(timeout=5)
                acc = local_db.allocate("VM-01")
                results.append(acc.username if acc else None)
            except BaseException as err:  # noqa: BLE001
                errors.append(err)
            finally:
                local_db.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        assert results.count("u1") == 2

        verify = AccountDB(db_path)
        try:
            running = [acc.username for acc in verify.get_all_accounts() if acc.assigned_machine == "VM-01"]
            assert running == ["u1"]
            assert verify.available_count == 1
            assert verify.in_use_count == 1
        finally:
            verify.close()


class TestMigration:
    def test_legacy_schema_without_legacy_rows_is_rebuilt(self, tmp_path: Path) -> None:
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE accounts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                username         TEXT    NOT NULL UNIQUE,
                password         TEXT    NOT NULL DEFAULT '',
                bind_email       TEXT    NOT NULL DEFAULT '',
                bind_email_pwd   TEXT    NOT NULL DEFAULT '',
                notes            TEXT    NOT NULL DEFAULT '',
                status           TEXT    NOT NULL DEFAULT '空闲'
                                 CHECK (status IN ('空闲', '使用中', '完成', '取号')),
                assigned_machine TEXT    NOT NULL DEFAULT '',
                level            INTEGER NOT NULL DEFAULT 0,
                jin_bi           TEXT    NOT NULL DEFAULT '0',
                completed_at     TEXT    DEFAULT NULL,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
        """)
        conn.commit()
        conn.close()

        db = AccountDB(db_path)
        try:
            inserted, skipped = db.load_from_text("u1----p1")
            assert inserted == 1
            assert skipped == 0
            allocated = db.allocate("VM-01")
            assert allocated is not None
            schema_sql = db._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='accounts'",
            ).fetchone()[0]
            assert "空闲中" in schema_sql
            assert "运行中" in schema_sql
        finally:
            db.close()


class TestUpsertFromSync:
    """slave ACCOUNT_SYNC → upsert_from_sync 测试

    职责：新账号插入为空闲中（或已封禁/已完成）；
    已有账号仅更新 level/jin_bi（非运行中）+ 封禁检测；
    不改已有账号的 status / assigned_machine。
    """

    def test_insert_new_as_idle(self, db: AccountDB) -> None:
        """不存在的账号插入为空闲中"""
        accounts = [
            {"username": "u1", "password": "p1", "level": 10, "jin_bi": "500",
             "is_banned": False, "is_active": False},
        ]
        inserted, updated = db.upsert_from_sync("VM-01", accounts)
        assert inserted == 1
        assert updated == 0
        acc = db.get_all_accounts()[0]
        assert acc.status == AccountStatus.IDLE
        assert acc.assigned_machine == ""  # 不绑定机器

    def test_insert_new_banned(self, db: AccountDB) -> None:
        """新账号 is_banned → 已封禁"""
        accounts = [
            {"username": "u1", "password": "p1", "level": 3, "jin_bi": "0",
             "is_banned": True, "is_active": False},
        ]
        db.upsert_from_sync("VM-01", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.status == AccountStatus.BANNED

    def test_insert_new_completed_by_threshold(self, db: AccountDB) -> None:
        """新账号 is_active=False + level >= threshold → 已完成"""
        accounts = [
            {"username": "u1", "password": "p1", "level": 18, "jin_bi": "50000",
             "is_banned": False, "is_active": False},
        ]
        db.upsert_from_sync("VM-01", accounts, level_threshold=18)
        acc = db.get_all_accounts()[0]
        assert acc.status == AccountStatus.COMPLETED
        assert acc.completed_at is not None

    def test_insert_active_still_idle(self, db: AccountDB) -> None:
        """新账号 is_active=True 也插为空闲中（不自动绑定）"""
        accounts = [
            {"username": "u1", "password": "p1", "level": 5, "jin_bi": "200",
             "is_banned": False, "is_active": True},
        ]
        db.upsert_from_sync("VM-01", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.status == AccountStatus.IDLE
        assert acc.assigned_machine == ""

    def test_insert_new_account_preserves_login_time(self, db: AccountDB) -> None:
        accounts = [
            {"username": "u1", "level": 5, "jin_bi": "200",
             "is_banned": False, "is_active": True, "login_at": "2026-03-24 10:00:00"},
        ]
        db.upsert_from_sync("VM-01", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.last_login_at is not None
        assert acc.last_login_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-03-24 10:00:00"

    def test_update_existing_accounts(self, db: AccountDB) -> None:
        """已存在的空闲账号应更新 level/jin_bi"""
        db.import_fresh("u1----p1")
        accounts = [
            {"username": "u1", "level": 20, "jin_bi": "9999",
             "is_banned": False, "is_active": False},
        ]
        inserted, updated = db.upsert_from_sync("VM-01", accounts)
        assert inserted == 0
        assert updated == 1
        acc = db.get_all_accounts()[0]
        assert acc.level == 20
        assert acc.jin_bi == "9999"
        assert acc.status == AccountStatus.IDLE

    def test_update_existing_account_login_time(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        accounts = [
            {"username": "u1", "level": 20, "jin_bi": "9999",
             "is_banned": False, "is_active": False, "login_at": "2026-03-24 10:00:00"},
        ]
        db.upsert_from_sync("VM-01", accounts)
        accounts[0]["login_at"] = "2026-03-24 11:00:00"
        db.upsert_from_sync("VM-01", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.last_login_at is not None
        assert acc.last_login_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-03-24 11:00:00"

    def test_active_account_login_time_allows_runtime_correction_to_earlier(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        db._conn.execute(
            "UPDATE accounts SET last_login_at='2026-03-24 17:31:00' WHERE username='u1'"
        )
        db._conn.commit()
        accounts = [
            {"username": "u1", "level": 20, "jin_bi": "9999",
             "is_banned": False, "is_active": True, "login_at": "2026-03-24 14:04:00"},
        ]

        db.upsert_from_sync("VM-01", accounts)

        acc = db.get_all_accounts()[0]
        assert acc.last_login_at is not None
        assert acc.last_login_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-03-24 14:04:00"

    def test_sync_does_not_change_status(self, db: AccountDB) -> None:
        """SYNC 不改变已有账号状态"""
        db.import_fresh("u1----p1")
        accounts = [
            {"username": "u1", "level": 10, "jin_bi": "500",
             "is_banned": False, "is_active": True},
        ]
        db.upsert_from_sync("VM-01", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.status == AccountStatus.IDLE

    def test_sync_does_not_change_assigned_machine(self, db: AccountDB) -> None:
        """SYNC 不改变 assigned_machine"""
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        accounts = [
            {"username": "u1", "level": 15, "jin_bi": "3000",
             "is_banned": False, "is_active": False},
        ]
        db.upsert_from_sync("VM-02", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.assigned_machine == "VM-01"

    def test_banned_updates_existing_status(self, db: AccountDB) -> None:
        """已有账号变为封禁"""
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        accounts = [
            {"username": "u1", "level": 10, "jin_bi": "100",
             "is_banned": True, "is_active": False},
        ]
        db.upsert_from_sync("VM-01", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.status == AccountStatus.BANNED

    def test_banned_from_idle(self, db: AccountDB) -> None:
        """空闲中账号也能被封禁"""
        db.import_fresh("u1----p1")
        accounts = [
            {"username": "u1", "level": 0, "jin_bi": "0",
             "is_banned": True, "is_active": False},
        ]
        db.upsert_from_sync("VM-01", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.status == AccountStatus.BANNED

    def test_skip_empty_username(self, db: AccountDB) -> None:
        """空用户名跳过"""
        db.import_fresh("valid----p")
        accounts = [
            {"username": "", "level": 1, "jin_bi": "0", "is_banned": False},
            {"username": "valid", "level": 5, "jin_bi": "200", "is_banned": False},
        ]
        _, updated = db.upsert_from_sync("VM-01", accounts)
        assert updated == 1
        assert db.total_count == 1

    def test_running_account_jinbi_skipped_by_sync(self, db: AccountDB) -> None:
        """运行中账号的 jin_bi/level 不被 ACCOUNT_SYNC 覆盖"""
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        db.update_from_status("VM-01", 15, "50000", "运行中")
        accounts = [
            {"username": "u1", "level": 10, "jin_bi": "3500",
             "is_banned": False, "is_active": True},
        ]
        db.upsert_from_sync("VM-01", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.jin_bi == "50000"
        assert acc.level == 15

    def test_running_account_login_time_still_updates_from_sync(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        accounts = [
            {"username": "u1", "level": 10, "jin_bi": "3500",
             "is_banned": False, "is_active": True, "login_at": "2026-03-24 10:00:00"},
        ]
        db.upsert_from_sync("VM-01", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.last_login_at is not None
        assert acc.last_login_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-03-24 10:00:00"

    def test_idle_account_jinbi_updates_normally(self, db: AccountDB) -> None:
        """非运行中账号 jin_bi 正常更新"""
        db.import_fresh("u1----p1")
        accounts = [
            {"username": "u1", "level": 10, "jin_bi": "3500",
             "is_banned": False, "is_active": False},
        ]
        db.upsert_from_sync("VM-01", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.jin_bi == "3500"
        assert acc.level == 10

    def test_mixed_insert_and_update(self, db: AccountDB) -> None:
        """已知账号更新，未知账号插入"""
        db.import_fresh("u1----p1")
        accounts = [
            {"username": "u1", "level": 15, "jin_bi": "3000",
             "is_banned": False, "is_active": False},
            {"username": "u2", "password": "p2", "level": 8, "jin_bi": "600",
             "is_banned": False, "is_active": False},
        ]
        inserted, updated = db.upsert_from_sync("VM-01", accounts)
        assert inserted == 1
        assert updated == 1
        assert db.total_count == 2

    def test_completed_restored_when_active(self, db: AccountDB) -> None:
        """已完成账号变为 is_active=true → 恢复为空闲中"""
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        db.complete("VM-01", level=18)
        assert db.get_all_accounts()[0].status == AccountStatus.COMPLETED
        accounts = [
            {"username": "u1", "level": 5, "jin_bi": "0",
             "is_banned": False, "is_active": True},
        ]
        db.upsert_from_sync("VM-01", accounts)
        acc = db.get_all_accounts()[0]
        assert acc.status == AccountStatus.IDLE
        assert acc.completed_at is None


class TestCounts:
    def test_counts_initial(self, db: AccountDB) -> None:
        assert db.total_count == 0
        assert db.available_count == 0
        assert db.in_use_count == 0
        assert db.completed_count == 0

    def test_counts_lifecycle(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1\nu2----p2\nu3----p3")
        assert db.total_count == 3
        assert db.available_count == 3

        db.allocate("VM-01")
        assert db.available_count == 2
        assert db.in_use_count == 1

        db.complete("VM-01", level=10)
        assert db.in_use_count == 0
        assert db.completed_count == 1

        db.allocate("VM-02")
        db.release("VM-02")
        assert db.available_count == 2
        assert db.in_use_count == 0


class TestPlatformUpload:
    """平台上传相关方法测试"""

    def test_uploaded_at_column_exists(self, db: AccountDB) -> None:
        """新建数据库应含 uploaded_at 列"""
        cur = db._conn.execute("PRAGMA table_info(accounts)")
        columns = {row[1] for row in cur.fetchall()}
        assert "uploaded_at" in columns

    def test_uploaded_at_migration(self, tmp_path: Path) -> None:
        """旧数据库打开后自动添加 uploaded_at 列"""
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE accounts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                username         TEXT    NOT NULL UNIQUE,
                password         TEXT    NOT NULL DEFAULT '',
                bind_email       TEXT    NOT NULL DEFAULT '',
                bind_email_pwd   TEXT    NOT NULL DEFAULT '',
                notes            TEXT    NOT NULL DEFAULT '',
                status           TEXT    NOT NULL DEFAULT '空闲中'
                                 CHECK (status IN ('空闲中', '运行中', '已完成', '已取号', '已封禁')),
                assigned_machine TEXT    NOT NULL DEFAULT '',
                level            INTEGER NOT NULL DEFAULT 0,
                jin_bi           TEXT    NOT NULL DEFAULT '0',
                completed_at     TEXT    DEFAULT NULL,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
        """)
        conn.execute("INSERT INTO accounts (username, password) VALUES ('u1', 'p1')")
        conn.commit()
        conn.close()

        db = AccountDB(db_path)
        try:
            cur = db._conn.execute("PRAGMA table_info(accounts)")
            columns = {row[1] for row in cur.fetchall()}
            assert "uploaded_at" in columns
            # 已有数据未受影响
            assert db.total_count == 1
        finally:
            db.close()

    def test_last_login_at_column_exists(self, db: AccountDB) -> None:
        cur = db._conn.execute("PRAGMA table_info(accounts)")
        columns = {row[1] for row in cur.fetchall()}
        assert "last_login_at" in columns

    def test_last_login_at_migration(self, tmp_path: Path) -> None:
        db_path = tmp_path / "old-login.db"
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE accounts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                username         TEXT    NOT NULL UNIQUE,
                password         TEXT    NOT NULL DEFAULT '',
                bind_email       TEXT    NOT NULL DEFAULT '',
                bind_email_pwd   TEXT    NOT NULL DEFAULT '',
                notes            TEXT    NOT NULL DEFAULT '',
                status           TEXT    NOT NULL DEFAULT '空闲中'
                                 CHECK (status IN ('空闲中', '运行中', '已完成', '已取号', '已封禁')),
                assigned_machine TEXT    NOT NULL DEFAULT '',
                level            INTEGER NOT NULL DEFAULT 0,
                jin_bi           TEXT    NOT NULL DEFAULT '0',
                completed_at     TEXT    DEFAULT NULL,
                uploaded_at      TEXT    DEFAULT NULL,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
        """)
        conn.execute("INSERT INTO accounts (username, password) VALUES ('u1', 'p1')")
        conn.commit()
        conn.close()

        db = AccountDB(db_path)
        try:
            cur = db._conn.execute("PRAGMA table_info(accounts)")
            columns = {row[1] for row in cur.fetchall()}
            assert "last_login_at" in columns
            assert db.total_count == 1
        finally:
            db.close()

    def test_get_completed_not_uploaded(self, db: AccountDB) -> None:
        """已完成且未上传的账号应被查出"""
        db.import_fresh("u1----p1\nu2----p2\nu3----p3")
        db.allocate("VM-01")
        db.complete("VM-01", level=18)
        db.allocate("VM-02")
        db.complete("VM-02", level=20)
        pending = db.get_completed_not_uploaded()
        assert len(pending) == 2
        assert {a.username for a in pending} == {"u1", "u2"}

    def test_get_completed_not_uploaded_excludes_uploaded(self, db: AccountDB) -> None:
        """已标记上传的账号不在结果中"""
        db.import_fresh("u1----p1\nu2----p2")
        db.allocate("VM-01")
        db.complete("VM-01", level=18)
        db.allocate("VM-02")
        db.complete("VM-02", level=20)
        db.mark_uploaded(["u1"])
        pending = db.get_completed_not_uploaded()
        assert len(pending) == 1
        assert pending[0].username == "u2"

    def test_get_completed_not_uploaded_respects_limit(self, db: AccountDB) -> None:
        """limit 应限制单次上传批量，避免 >200 账号一次性处理。"""
        lines = "\n".join(f"u{i}----p{i}" for i in range(205))
        db.import_fresh(lines)
        db._conn.execute(
            "UPDATE accounts SET status='已完成', completed_at='2026-03-25 10:00:00'"
        )
        db._conn.commit()
        db._refresh_counts()

        pending = db.get_completed_not_uploaded(limit=200)

        assert len(pending) == 200
        assert pending[0].username == "u0"
        assert pending[-1].username == "u199"

    def test_mark_uploaded(self, db: AccountDB) -> None:
        """mark_uploaded 设置 uploaded_at 时间戳"""
        db.import_fresh("u1----p1\nu2----p2")
        db.allocate("VM-01")
        db.complete("VM-01", level=18)
        count = db.mark_uploaded(["u1"])
        assert count == 1
        row = db._conn.execute(
            "SELECT uploaded_at FROM accounts WHERE username='u1'"
        ).fetchone()
        assert row[0] is not None

    def test_mark_uploaded_idempotent(self, db: AccountDB) -> None:
        """重复上传同一个账号不会重复标记"""
        db.import_fresh("u1----p1")
        db.allocate("VM-01")
        db.complete("VM-01", level=18)
        db.mark_uploaded(["u1"])
        count = db.mark_uploaded(["u1"])
        assert count == 0

    def test_mark_uploaded_empty_list(self, db: AccountDB) -> None:
        """空列表不操作"""
        assert db.mark_uploaded([]) == 0

    def test_mark_taken_by_platform(self, db: AccountDB) -> None:
        """平台取号 → 状态流转为已取号"""
        db.import_fresh("u1----p1\nu2----p2")
        db.allocate("VM-01")
        db.complete("VM-01", level=18)
        db.allocate("VM-02")
        db.complete("VM-02", level=20)
        count = db.mark_taken_by_platform(["u1"])
        assert count == 1
        accs = {a.username: a for a in db.get_all_accounts()}
        assert accs["u1"].status == AccountStatus.FETCHED
        assert accs["u2"].status == AccountStatus.COMPLETED

    def test_mark_taken_only_affects_completed(self, db: AccountDB) -> None:
        """mark_taken_by_platform 只影响已完成状态的账号"""
        db.import_fresh("u1----p1")
        # u1 是空闲中
        count = db.mark_taken_by_platform(["u1"])
        assert count == 0
        assert db.get_all_accounts()[0].status == AccountStatus.IDLE


class TestSoftDelete:
    """软删除相关测试：防止 slave sync 复活已删除账号"""

    def test_delete_marks_as_deleted(self, db: AccountDB) -> None:
        """delete_by_usernames 将账号标记为 '已删除' 而非物理删除"""
        db.import_fresh("u1----p1\nu2----p2")
        deleted = db.delete_by_usernames(["u1"])
        assert deleted == 1
        # 软删除的账号不出现在 get_all_accounts
        accs = db.get_all_accounts()
        assert len(accs) == 1
        assert accs[0].username == "u2"
        # 但物理行仍然存在
        row = db._conn.execute(
            "SELECT status FROM accounts WHERE username='u1'"
        ).fetchone()
        assert row is not None
        assert row["status"] == "已删除"

    def test_deleted_excluded_from_total_count(self, db: AccountDB) -> None:
        """已删除账号不计入 total_count"""
        db.import_fresh("u1----p1\nu2----p2\nu3----p3")
        db.delete_by_usernames(["u1"])
        assert db.total_count == 2
        assert db.available_count == 2

    def test_sync_does_not_resurrect_deleted(self, db: AccountDB) -> None:
        """slave ACCOUNT_SYNC 不会复活已软删除的账号"""
        db.import_fresh("u1----p1\nu2----p2")
        db.delete_by_usernames(["u1"])
        # slave 同步上来同一个账号
        accounts = [
            {"username": "u1", "password": "p1", "level": 10, "jin_bi": "500",
             "is_banned": False, "is_active": False},
        ]
        inserted, updated = db.upsert_from_sync("VM-01", accounts)
        assert inserted == 0
        assert updated == 0
        # 仍然只有 u2 可见
        accs = db.get_all_accounts()
        assert len(accs) == 1
        assert accs[0].username == "u2"

    def test_sync_does_not_resurrect_deleted_even_active(self, db: AccountDB) -> None:
        """即使 slave 报告已删除账号为 is_active，也不复活"""
        db.import_fresh("u1----p1")
        db.delete_by_usernames(["u1"])
        accounts = [
            {"username": "u1", "password": "p1", "level": 5, "jin_bi": "200",
             "is_banned": False, "is_active": True},
        ]
        inserted, updated = db.upsert_from_sync("VM-01", accounts)
        assert inserted == 0
        assert updated == 0
        assert db.total_count == 0

    def test_manual_import_resurrects_deleted(self, db: AccountDB) -> None:
        """用户手动导入可以恢复软删除的账号"""
        db.import_fresh("u1----p1\nu2----p2")
        db.delete_by_usernames(["u1"])
        assert db.total_count == 1
        # 手动重新导入
        inserted, skipped = db.load_from_text("u1----p1")
        assert inserted == 1
        assert skipped == 0
        assert db.total_count == 2
        accs = {a.username: a for a in db.get_all_accounts()}
        assert accs["u1"].status == AccountStatus.IDLE

    def test_import_fresh_clears_deleted(self, db: AccountDB) -> None:
        """import_fresh 清空所有数据包括软删除记录"""
        db.import_fresh("u1----p1\nu2----p2")
        db.delete_by_usernames(["u1"])
        db.import_fresh("u1----p1\nu3----p3")
        assert db.total_count == 2
        accs = {a.username: a for a in db.get_all_accounts()}
        assert "u1" in accs
        assert "u3" in accs

    def test_clear_all_hard_deletes(self, db: AccountDB) -> None:
        """clear_all 物理删除所有记录（包含软删除）"""
        db.import_fresh("u1----p1\nu2----p2")
        db.delete_by_usernames(["u1"])
        db.clear_all()
        assert db.total_count == 0
        row = db._conn.execute(
            "SELECT COUNT(*) FROM accounts"
        ).fetchone()
        assert row[0] == 0

    def test_purge_deleted(self, db: AccountDB) -> None:
        """purge_deleted 物理移除所有软删除记录"""
        db.import_fresh("u1----p1\nu2----p2\nu3----p3")
        db.delete_by_usernames(["u1", "u2"])
        purged = db.purge_deleted()
        assert purged == 2
        # 物理行已不存在
        row = db._conn.execute(
            "SELECT COUNT(*) FROM accounts"
        ).fetchone()
        assert row[0] == 1

    def test_export_all_excludes_deleted(self, db: AccountDB) -> None:
        """export_all 不导出已删除账号"""
        db.import_fresh("u1----p1\nu2----p2")
        db.delete_by_usernames(["u1"])
        text = db.export_all()
        assert "u1" not in text
        assert "u2" in text

    def test_delete_idempotent(self, db: AccountDB) -> None:
        """重复删除同一账号，第二次返回 0"""
        db.import_fresh("u1----p1")
        assert db.delete_by_usernames(["u1"]) == 1
        assert db.delete_by_usernames(["u1"]) == 0
