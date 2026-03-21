"""AccountDB 单元测试"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from common.models import AccountStatus
from master.app.core.account_db import AccountDB

_SAMPLE = "u1----p1----e1----ep1----note1\nu2----p2\nu3----p3----e3----ep3"


@pytest.fixture()
def db(tmp_path: Path) -> AccountDB:
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
        text = db.export_completed()
        lines = text.splitlines()
        assert lines[0].startswith("账号----密码")  # 表头
        assert "u1----p1----e1----ep1----20----0----正常----无----" in text
        # u2 未完成，不在导出中
        assert "u2" not in text

    def test_export_empty(self, db: AccountDB) -> None:
        db.import_fresh("u1----p1")
        text = db.export_completed()
        lines = text.splitlines()
        assert len(lines) == 1  # 仅表头
        assert lines[0].startswith("账号----密码")


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
