"""账号池 SQLite 持久化管理器 — API 兼容原 AccountPool"""
from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from common.models import AccountInfo, AccountStatus

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS accounts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    username         TEXT    NOT NULL UNIQUE,
    password         TEXT    NOT NULL DEFAULT '',
    bind_email       TEXT    NOT NULL DEFAULT '',
    bind_email_pwd   TEXT    NOT NULL DEFAULT '',
    notes            TEXT    NOT NULL DEFAULT '',
    status           TEXT    NOT NULL DEFAULT '空闲'
                     CHECK (status IN ('空闲', '使用中', '已完成')),
    assigned_machine TEXT    NOT NULL DEFAULT '',
    level            INTEGER NOT NULL DEFAULT 0,
    jin_bi           TEXT    NOT NULL DEFAULT '0',
    completed_at     TEXT    DEFAULT NULL,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_status ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_machine ON accounts(assigned_machine) WHERE assigned_machine != '';

CREATE TRIGGER IF NOT EXISTS trg_updated AFTER UPDATE ON accounts
BEGIN
    UPDATE accounts SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;
"""


class AccountDB(QObject):
    """SQLite 持久化账号池，信号接口兼容 AccountPool"""

    pool_changed = pyqtSignal()

    def __init__(self, db_path: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # 缓存计数
        self._total = 0
        self._available = 0
        self._in_use = 0
        self._completed = 0
        self._refresh_counts()

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    # ── 导入 ───────────────────────────────────────────────

    def import_fresh(self, text: str) -> None:
        """清空 → 全量导入（大屏文本框同步用）"""
        self._conn.execute("DELETE FROM accounts")
        self._insert_lines(text)
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()

    def load_from_text(self, text: str) -> None:
        """合并导入 INSERT OR IGNORE（增量添加）"""
        self._insert_lines(text)
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()

    def load_from_file(self, path: str | Path) -> None:
        """读文件 → load_from_text"""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            raise OSError(f"无法读取账号文件: {e}") from e
        self.load_from_text(text)

    def _insert_lines(self, text: str) -> None:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            acc = AccountInfo.from_line(line)
            if not acc.username:
                continue
            self._conn.execute(
                "INSERT OR IGNORE INTO accounts "
                "(username, password, bind_email, bind_email_pwd, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (acc.username, acc.password, acc.bind_email,
                 acc.bind_email_password, acc.notes),
            )

    # ── 分配 / 回收 ───────────────────────────────────────

    def allocate(self, machine_name: str) -> AccountInfo | None:
        """取第一个空闲账号绑定到机器，原子操作"""
        cur = self._conn.execute(
            "UPDATE accounts SET status='使用中', assigned_machine=? "
            "WHERE id = (SELECT id FROM accounts WHERE status='空闲' LIMIT 1) "
            "RETURNING *",
            (machine_name,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()
        return self._row_to_info(row)

    def complete(self, machine_name: str, level: int = 0) -> None:
        """使用中 → 已完成"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            "UPDATE accounts SET status='已完成', level=?, completed_at=? "
            "WHERE status='使用中' AND assigned_machine=?",
            (level, now, machine_name),
        )
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()

    def release(self, machine_name: str) -> None:
        """使用中 → 空闲，解绑机器"""
        self._conn.execute(
            "UPDATE accounts SET status='空闲', assigned_machine='' "
            "WHERE status='使用中' AND assigned_machine=?",
            (machine_name,),
        )
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()

    def update_from_status(
        self, machine_name: str, level: int, jin_bi: str, state: str,
    ) -> None:
        """slave STATUS 上报 → 更新绑定账号的等级/金币，已完成则自动流转"""
        changed = self._conn.execute(
            "UPDATE accounts SET level=?, jin_bi=? "
            "WHERE status='使用中' AND assigned_machine=?",
            (level, jin_bi, machine_name),
        ).rowcount
        if not changed:
            return
        if state == "已完成":
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._conn.execute(
                "UPDATE accounts SET status='已完成', completed_at=? "
                "WHERE status='使用中' AND assigned_machine=?",
                (now, machine_name),
            )
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()

    # ── 查询 ───────────────────────────────────────────────

    def get_account_for_machine(self, machine_name: str) -> AccountInfo | None:
        """查已绑定的使用中账号"""
        cur = self._conn.execute(
            "SELECT * FROM accounts WHERE assigned_machine=? AND status='使用中'",
            (machine_name,),
        )
        row = cur.fetchone()
        return self._row_to_info(row) if row else None

    def get_all_accounts(self) -> list[AccountInfo]:
        """全量查询，按 id 排序"""
        cur = self._conn.execute("SELECT * FROM accounts ORDER BY id")
        return [self._row_to_info(r) for r in cur.fetchall()]

    # ── 导出 ───────────────────────────────────────────────

    def export_completed(self) -> str:
        """导出已完成账号，对齐原版 9 字段格式"""
        cur = self._conn.execute(
            "SELECT * FROM accounts WHERE status='已完成' ORDER BY id",
        )
        lines: list[str] = []
        for row in cur.fetchall():
            time_str = row["completed_at"] or "无"
            lines.append(
                f"{row['username']}----{row['password']}----{row['bind_email']}----"
                f"{row['bind_email_pwd']}----{row['level']}----{row['jin_bi']}----"
                f"正常----无----{time_str}"
            )
        return "\n".join(lines)

    # ── 清空 ───────────────────────────────────────────────

    def clear_all(self) -> None:
        """清空所有账号"""
        self._conn.execute("DELETE FROM accounts")
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()

    # ── 统计属性 ───────────────────────────────────────────

    @property
    def total_count(self) -> int:
        return self._total

    @property
    def available_count(self) -> int:
        return self._available

    @property
    def in_use_count(self) -> int:
        return self._in_use

    @property
    def completed_count(self) -> int:
        return self._completed

    # ── 内部方法 ───────────────────────────────────────────

    def _refresh_counts(self) -> None:
        cur = self._conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM accounts GROUP BY status",
        )
        counts = {row["status"]: row["cnt"] for row in cur.fetchall()}
        self._total = sum(counts.values())
        self._available = counts.get("空闲", 0)
        self._in_use = counts.get("使用中", 0)
        self._completed = counts.get("已完成", 0)

    @staticmethod
    def _row_to_info(row: sqlite3.Row) -> AccountInfo:
        completed_at = None
        if row["completed_at"]:
            with contextlib.suppress(ValueError):
                completed_at = datetime.strptime(
                    row["completed_at"], "%Y-%m-%d %H:%M:%S",
                )
        return AccountInfo(
            username=row["username"],
            password=row["password"],
            bind_email=row["bind_email"],
            bind_email_password=row["bind_email_pwd"],
            notes=row["notes"],
            status=AccountStatus(row["status"]),
            assigned_machine=row["assigned_machine"],
            level=row["level"],
            jin_bi=row["jin_bi"],
            completed_at=completed_at,
        )
