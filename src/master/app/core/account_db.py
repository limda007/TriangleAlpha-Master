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
CREATE INDEX IF NOT EXISTS idx_status ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_machine ON accounts(assigned_machine) WHERE assigned_machine != '';

CREATE TRIGGER IF NOT EXISTS trg_updated AFTER UPDATE ON accounts
BEGIN
    UPDATE accounts SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

_LEGACY_STATUS_MAP = {
    "空闲": "空闲中",
    "使用中": "运行中",
    "运行": "运行中",
    "完成": "已完成",
    "取号": "已取号",
}
_EXPECTED_STATUS_TOKENS = ("'空闲中'", "'运行中'", "'已完成'", "'已取号'", "'已封禁'")


class AccountDB(QObject):
    """SQLite 持久化账号池

    TODO: 大规模部署（>5000 账号 + >100 节点）时应将 DB 操作移到工作线程
    """

    pool_changed = pyqtSignal()

    def __init__(self, db_path: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # 迁移：修正旧版状态值
        self._migrate_legacy_status()
        # 迁移：添加 uploaded_at 列
        self._ensure_uploaded_at_column()
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

    # ── 配置键值存取 ──

    def get_config(self, key: str, default: str = "") -> str:
        """读取配置项"""
        row = self._conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,),
        ).fetchone()
        return row["value"] if row else default

    def set_config(self, key: str, value: str) -> None:
        """写入配置项（upsert）"""
        self._conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    # ── 导入 ───────────────────────────────────────────────

    def import_fresh(self, text: str) -> None:
        """清空 → 全量导入（大屏文本框同步用）"""
        self._conn.execute("DELETE FROM accounts")
        self._insert_lines(text)
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()

    def load_from_text(self, text: str) -> tuple[int, int]:
        """合并导入 INSERT OR IGNORE（增量添加）

        Returns:
            (inserted, skipped) 元组
        """
        inserted, skipped = self._insert_lines(text)
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()
        return inserted, skipped

    def load_from_file(self, path: str | Path) -> tuple[int, int]:
        """读文件 → load_from_text"""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            raise OSError(f"无法读取账号文件: {e}") from e
        return self.load_from_text(text)

    def _insert_lines(self, text: str) -> tuple[int, int]:
        inserted = 0
        skipped = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            acc = AccountInfo.from_line(line)
            if not acc.username:
                continue
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO accounts "
                "(username, password, bind_email, bind_email_pwd, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (acc.username, acc.password, acc.bind_email,
                 acc.bind_email_password, acc.notes),
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        return inserted, skipped

    # ── 分配 / 回收 ───────────────────────────────────────

    def allocate(self, machine_name: str) -> AccountInfo | None:
        """取第一个空闲账号绑定到机器，原子操作"""
        cur = self._conn.execute(
            "UPDATE accounts SET status='运行中', assigned_machine=? "
            "WHERE id = (SELECT id FROM accounts WHERE status='空闲中' ORDER BY id LIMIT 1) "
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
        """运行中 → 已完成"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            "UPDATE accounts SET status='已完成', level=?, completed_at=? "
            "WHERE status='运行中' AND assigned_machine=?",
            (level, now, machine_name),
        )
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()

    def release(self, machine_name: str) -> None:
        """运行中 → 空闲中，解绑机器"""
        self._conn.execute(
            "UPDATE accounts SET status='空闲中', assigned_machine='' "
            "WHERE status='运行中' AND assigned_machine=?",
            (machine_name,),
        )
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()

    def update_from_status(
        self, machine_name: str, level: int, jin_bi: str, state: str,
        *, current_account: str = "",
    ) -> None:
        """slave STATUS 上报 → 更新绑定账号的等级/金币，已完成则自动流转。

        若 assigned_machine 找不到对应账号但 current_account 匹配，自动绑定。

        防护措施：
        - level 使用 MAX 防止等级回退（等级只会增长）
        - jin_bi 仅在新值非零时更新，防止 IPC 超时/脚本停止导致零值覆盖
        """
        # 零值保护：level 只增不减，jin_bi 零值不覆盖
        if jin_bi and jin_bi != "0":
            changed = self._conn.execute(
                "UPDATE accounts SET level=MAX(level, ?), jin_bi=? "
                "WHERE status='运行中' AND assigned_machine=?",
                (level, jin_bi, machine_name),
            ).rowcount
        else:
            changed = self._conn.execute(
                "UPDATE accounts SET level=MAX(level, ?) "
                "WHERE status='运行中' AND assigned_machine=?",
                (level, machine_name),
            ).rowcount
        if not changed and current_account:
            # 账号未经 allocate，但 TestDemo 已在使用 → 自动绑定
            # 自动绑定时也应用零值保护
            if jin_bi and jin_bi != "0":
                changed = self._conn.execute(
                    "UPDATE accounts SET level=MAX(level, ?), jin_bi=?, "
                    "status='运行中', assigned_machine=? "
                    "WHERE username=? AND status IN ('空闲中', '运行中')",
                    (level, jin_bi, machine_name, current_account),
                ).rowcount
            else:
                changed = self._conn.execute(
                    "UPDATE accounts SET level=MAX(level, ?), "
                    "status='运行中', assigned_machine=? "
                    "WHERE username=? AND status IN ('空闲中', '运行中')",
                    (level, machine_name, current_account),
                ).rowcount
        if not changed:
            return
        if state == "已完成":
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._conn.execute(
                "UPDATE accounts SET status='已完成', completed_at=? "
                "WHERE status='运行中' AND assigned_machine=?",
                (now, machine_name),
            )
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()

    def upsert_from_sync(
        self, machine_name: str, accounts: list[dict[str, object]],
        *, level_threshold: int = 0,
    ) -> tuple[int, int]:
        """从 slave 的 accounts.json 同步账号数据。

        职责：
        - 不存在的账号 → 插入为空闲中（或已封禁/已完成）
        - 已存在的账号 → 仅更新 level/jin_bi（非运行中）+ 封禁检测
        - 不改已有账号的 status / assigned_machine

        Returns:
            (inserted, updated) 元组
        """
        inserted = updated = 0
        for acc in accounts:
            username = str(acc.get("username", "")).strip()
            if not username:
                continue
            is_banned = bool(acc.get("is_banned"))
            is_active = bool(acc.get("is_active"))
            level_raw = str(acc.get("level", "0"))
            level = int(level_raw) if level_raw.isdigit() else 0
            jin_bi = str(acc.get("jin_bi", "0"))

            existing = self._conn.execute(
                "SELECT id, status FROM accounts WHERE username = ?",
                (username,),
            ).fetchone()

            if existing is None:
                # 新账号：确定初始状态
                if is_banned:
                    status = "已封禁"
                elif (not is_active and level_threshold > 0
                      and level >= level_threshold):
                    status = "已完成"
                else:
                    status = "空闲中"
                now = (datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                       if status == "已完成" else None)
                self._conn.execute(
                    "INSERT INTO accounts "
                    "(username, password, bind_email, bind_email_pwd, "
                    " status, level, jin_bi, completed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (username, str(acc.get("password", "")),
                     str(acc.get("bind_email", "")),
                     str(acc.get("bind_email_pwd", "")),
                     status, level, jin_bi, now),
                )
                inserted += 1
                continue

            # 已有账号：封禁检测
            if is_banned and existing["status"] != "已封禁":
                self._conn.execute(
                    "UPDATE accounts SET status='已封禁' WHERE id = ?",
                    (existing["id"],),
                )
                updated += 1
                continue

            # 已完成但 is_active=true → 账号被重新使用，恢复为空闲中
            if is_active and existing["status"] == "已完成":
                self._conn.execute(
                    "UPDATE accounts SET status='空闲中', assigned_machine='', "
                    "completed_at=NULL, level=?, jin_bi=? WHERE id = ?",
                    (level, jin_bi, existing["id"]),
                )
                updated += 1
                continue

            # 运行中账号：检查是否已达到下号等级（补偿 master 离线期间错过的完成上报）
            if existing["status"] == "运行中":
                if (not is_active and level_threshold > 0
                        and level >= level_threshold):
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._conn.execute(
                        "UPDATE accounts SET status='已完成', level=?, jin_bi=?, "
                        "completed_at=? WHERE id = ?",
                        (level, jin_bi, now, existing["id"]),
                    )
                    updated += 1
                continue

            self._conn.execute(
                "UPDATE accounts SET level = ?, jin_bi = ? WHERE id = ?",
                (level, jin_bi, existing["id"]),
            )
            updated += 1

        if inserted or updated:
            self._conn.commit()
            self._refresh_counts()
            self.pool_changed.emit()
        return inserted, updated

    # ── 查询 ───────────────────────────────────────────────

    def get_account_for_machine(self, machine_name: str) -> AccountInfo | None:
        """查已绑定的运行中账号"""
        cur = self._conn.execute(
            "SELECT * FROM accounts WHERE assigned_machine=? AND status='运行中'",
            (machine_name,),
        )
        row = cur.fetchone()
        return self._row_to_info(row) if row else None

    def get_all_accounts(self) -> list[AccountInfo]:
        """全量查询，按 id 排序"""
        cur = self._conn.execute("SELECT * FROM accounts ORDER BY id")
        return [self._row_to_info(r) for r in cur.fetchall()]

    # ── 导出 ───────────────────────────────────────────────

    def get_completed_not_uploaded(self) -> list[AccountInfo]:
        """查询已完成但未上传到平台的账号"""
        cur = self._conn.execute(
            "SELECT * FROM accounts WHERE status='已完成' AND uploaded_at IS NULL "
            "ORDER BY id",
        )
        return [self._row_to_info(r) for r in cur.fetchall()]

    def mark_uploaded(self, usernames: list[str]) -> int:
        """标记账号已上传到平台（批量，事务内）"""
        if not usernames:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total = 0
        for username in usernames:
            total += self._conn.execute(
                "UPDATE accounts SET uploaded_at=? "
                "WHERE username=? AND status='已完成' AND uploaded_at IS NULL",
                (now, username),
            ).rowcount
        self._conn.commit()
        return total

    def mark_taken_by_platform(self, usernames: list[str]) -> int:
        """平台确认账号已被取号 → 本地状态流转为 '已取号'"""
        if not usernames:
            return 0
        total = 0
        for username in usernames:
            total += self._conn.execute(
                "UPDATE accounts SET status='已取号' "
                "WHERE username=? AND status='已完成'",
                (username,),
            ).rowcount
        if total:
            self._conn.commit()
            self._refresh_counts()
            self.pool_changed.emit()
        return total

    def export_completed(self, mark_fetched: bool = False) -> str:
        """导出已完成账号，对齐原版 9 字段格式（含表头）

        mark_fetched=True 时，导出后将状态流转为 '已取号'。
        """
        cur = self._conn.execute(
            "SELECT * FROM accounts WHERE status='已完成' ORDER BY id",
        )
        header = "账号----密码----邮箱----邮箱密码----等级----金币----状态----备注----完成时间"
        lines: list[str] = [header]
        for row in cur.fetchall():
            time_str = row["completed_at"] or "无"
            lines.append(
                f"{row['username']}----{row['password']}----{row['bind_email']}----"
                f"{row['bind_email_pwd']}----{row['level']}----{row['jin_bi']}----"
                f"正常----无----{time_str}"
            )
        if mark_fetched:
            self._conn.execute("UPDATE accounts SET status='已取号' WHERE status='已完成'")
            self._conn.commit()
            self._refresh_counts()
            self.pool_changed.emit()
        return "\n".join(lines)

    def export_all(self) -> str:
        """导出所有账号（含表头），不改变状态。"""
        cur = self._conn.execute("SELECT * FROM accounts ORDER BY id")
        header = "账号----密码----邮箱----邮箱密码----等级----金币----状态----备注----完成时间"
        lines: list[str] = [header]
        for row in cur.fetchall():
            time_str = row["completed_at"] or "无"
            lines.append(
                f"{row['username']}----{row['password']}----{row['bind_email']}----"
                f"{row['bind_email_pwd']}----{row['level']}----{row['jin_bi']}----"
                f"{row['status']}----{row['notes']}----{time_str}"
            )
        return "\n".join(lines)

    # ── 清空 ───────────────────────────────────────────────

    def clear_all(self) -> None:
        """清空所有账号"""
        self._conn.execute("DELETE FROM accounts")
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()

    def delete_by_usernames(self, usernames: list[str]) -> int:
        """按用户名批量删除，返回删除数"""
        if not usernames:
            return 0
        placeholders = ",".join("?" * len(usernames))
        cur = self._conn.execute(
            f"DELETE FROM accounts WHERE username IN ({placeholders})", usernames,  # noqa: S608
        )
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()
        return cur.rowcount

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

    def _ensure_uploaded_at_column(self) -> None:
        """迁移：为已有数据库添加 uploaded_at 列"""
        cur = self._conn.execute("PRAGMA table_info(accounts)")
        columns = {row[1] for row in cur.fetchall()}
        if "uploaded_at" not in columns:
            self._conn.execute(
                "ALTER TABLE accounts ADD COLUMN uploaded_at TEXT DEFAULT NULL"
            )
            self._conn.commit()

    def _migrate_legacy_status(self) -> None:
        """修正旧版数据库中的状态值和 CHECK 约束。"""
        if self._schema_requires_rebuild():
            self._rebuild_table_with_new_constraint()
            return
        for old, new in _LEGACY_STATUS_MAP.items():
            self._conn.execute(
                "UPDATE accounts SET status=? WHERE status=?", (new, old),
            )
        self._conn.commit()

    def _schema_requires_rebuild(self) -> bool:
        """检测 accounts 表约束是否仍停留在旧版状态集合。"""
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='accounts'",
        ).fetchone()
        if row is None or not row[0]:
            return False
        schema_sql = str(row[0])
        if not all(token in schema_sql for token in _EXPECTED_STATUS_TOKENS):
            return True
        return any(f"'{legacy}'" in schema_sql for legacy in _LEGACY_STATUS_MAP)

    def _rebuild_table_with_new_constraint(self) -> None:
        """重建表以更新 CHECK 约束（SQLite 不支持 ALTER CHECK）。"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts_new (
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
        """)
        # 迁移数据，同时映射旧状态值
        case_expr = "CASE status "
        for old, new in _LEGACY_STATUS_MAP.items():
            case_expr += f"WHEN '{old}' THEN '{new}' "
        case_expr += "ELSE status END"
        self._conn.execute(f"""
            INSERT OR IGNORE INTO accounts_new
                (username, password, bind_email, bind_email_pwd, notes,
                 status, assigned_machine, level, jin_bi,
                 completed_at, created_at, updated_at)
            SELECT username, password, bind_email, bind_email_pwd, notes,
                   {case_expr}, assigned_machine, level, jin_bi,
                   completed_at, created_at, updated_at
            FROM accounts
        """)
        self._conn.execute("DROP TABLE accounts")
        self._conn.execute("ALTER TABLE accounts_new RENAME TO accounts")
        # 重建索引和触发器
        self._conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_status ON accounts(status);
            CREATE INDEX IF NOT EXISTS idx_machine
                ON accounts(assigned_machine) WHERE assigned_machine != '';
            CREATE TRIGGER IF NOT EXISTS trg_updated AFTER UPDATE ON accounts
            BEGIN
                UPDATE accounts SET updated_at = datetime('now','localtime')
                    WHERE id = NEW.id;
            END;
        """)
        self._conn.commit()

    def _refresh_counts(self) -> None:
        cur = self._conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM accounts GROUP BY status",
        )
        counts = {row["status"]: row["cnt"] for row in cur.fetchall()}
        self._total = sum(counts.values())
        self._available = counts.get("空闲中", 0)
        self._in_use = counts.get("运行中", 0)
        self._completed = counts.get("已完成", 0)

    @staticmethod
    def _row_to_info(row: sqlite3.Row) -> AccountInfo:
        completed_at = None
        if row["completed_at"]:
            with contextlib.suppress(ValueError):
                completed_at = datetime.strptime(
                    row["completed_at"], "%Y-%m-%d %H:%M:%S",
                )
        updated_at = None
        if row["updated_at"]:
            with contextlib.suppress(ValueError):
                updated_at = datetime.strptime(
                    row["updated_at"], "%Y-%m-%d %H:%M:%S",
                )
        created_at = None
        if row["created_at"]:
            with contextlib.suppress(ValueError):
                created_at = datetime.strptime(
                    row["created_at"], "%Y-%m-%d %H:%M:%S",
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
            updated_at=updated_at,
            created_at=created_at,
        )
