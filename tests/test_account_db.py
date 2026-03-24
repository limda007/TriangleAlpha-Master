"""AccountDB 单元测试"""
from __future__ import annotations

import sqlite3
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
