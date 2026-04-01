"""账号池 SQLite 持久化管理器 — API 兼容原 AccountPool"""
from __future__ import annotations

import contextlib
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from PyQt6.QtCore import QObject, pyqtSignal

from common.models import EXPORT_ACCOUNT_HEADER, AccountInfo, AccountStatus

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS accounts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    username         TEXT    NOT NULL UNIQUE,
    password         TEXT    NOT NULL DEFAULT '',
    bind_email       TEXT    NOT NULL DEFAULT '',
    bind_email_pwd   TEXT    NOT NULL DEFAULT '',
    notes            TEXT    NOT NULL DEFAULT '',
    status           TEXT    NOT NULL DEFAULT '空闲中'
                     CHECK (status IN ('空闲中', '运行中', '已完成', '已取号', '已封禁', '已删除')),
    assigned_machine TEXT    NOT NULL DEFAULT '',
    level            INTEGER NOT NULL DEFAULT 0,
    jin_bi           TEXT    NOT NULL DEFAULT '0',
    completed_at     TEXT    DEFAULT NULL,
    uploaded_at      TEXT    DEFAULT NULL,
    last_login_at    TEXT    DEFAULT NULL,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_status ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_machine ON accounts(assigned_machine) WHERE assigned_machine != '';
CREATE INDEX IF NOT EXISTS idx_username_status ON accounts(username, status);

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
_EXPECTED_STATUS_TOKENS = ("'空闲中'", "'运行中'", "'已完成'", "'已取号'", "'已封禁'", "'已删除'")


class AccountDB(QObject):
    """SQLite 持久化账号池

    TODO: 大规模部署（>5000 账号 + >100 节点）时应将 DB 操作移到工作线程
    """

    pool_changed = pyqtSignal()
    _path_locks: ClassVar[dict[str, threading.RLock]] = {}
    _path_locks_guard: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, db_path: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db_path = str(db_path)
        self._closed = False
        self._conn: sqlite3.Connection = sqlite3.connect(
            self._db_path, timeout=10, isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._write_lock = self._get_write_lock(self._db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # 迁移：修正旧版状态值
        self._migrate_legacy_status()
        # 迁移：添加 uploaded_at 列
        self._ensure_uploaded_at_column()
        # 迁移：添加 last_login_at 列
        self._ensure_last_login_at_column()
        # 缓存计数
        self._total = 0
        self._available = 0
        self._in_use = 0
        self._completed = 0
        self._refresh_counts()

    def close(self) -> None:
        """关闭数据库连接"""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self._conn.close()

    @property
    def is_closed(self) -> bool:
        """连接是否已显式关闭。"""
        return self._closed

    # ── 配置键值存取 ──

    def get_config(self, key: str, default: str = "") -> str:
        """读取配置项"""
        if self._closed:
            return default
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
        rows: list[tuple[str, str, str, str, str]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            acc = AccountInfo.from_line(line)
            if not acc.username:
                continue
            rows.append(
                (
                    acc.username,
                    acc.password,
                    acc.bind_email,
                    acc.bind_email_password,
                    acc.notes,
                )
            )
        if not rows:
            return 0, 0
        # 恢复已软删除的账号（用户手动导入时允许复活）
        usernames = [r[0] for r in rows]
        placeholders = ",".join("?" * len(usernames))
        resurrected = self._conn.execute(
            f"UPDATE accounts SET status='空闲中', assigned_machine='' "
            f"WHERE username IN ({placeholders}) AND status='已删除'",  # noqa: S608
            usernames,
        ).rowcount
        before_changes = self._conn.total_changes
        self._conn.executemany(
            "INSERT OR IGNORE INTO accounts "
            "(username, password, bind_email, bind_email_pwd, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        inserted = self._conn.total_changes - before_changes
        skipped = len(rows) - inserted - resurrected
        return inserted + resurrected, max(0, skipped)

    # ── 分配 / 回收 ───────────────────────────────────────

    def allocate(self, machine_name: str) -> AccountInfo | None:
        """为机器分配账号；若该机器已有运行中账号则直接返回原绑定。"""
        machine = machine_name.strip()
        if not machine:
            return None

        changed = False
        with self._write_transaction():
            row = self._conn.execute(
                "SELECT * FROM accounts "
                "WHERE assigned_machine=? AND status='运行中' "
                "ORDER BY id LIMIT 1",
                (machine,),
            ).fetchone()
            if row is None:
                row = self._conn.execute(
                    "UPDATE accounts SET status='运行中', assigned_machine=? "
                    "WHERE id = (SELECT id FROM accounts WHERE status='空闲中' ORDER BY id LIMIT 1) "
                    "RETURNING *",
                    (machine,),
                ).fetchone()
                changed = row is not None
        if row is None:
            return None
        if changed:
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
        *, current_account: str = "", login_at: str = "",
    ) -> None:
        """slave STATUS 上报 → 更新绑定账号的等级/金币，已完成则自动流转。

        若 assigned_machine 找不到对应账号但 current_account 匹配，自动绑定。

        防护措施：
        - level 使用 MAX 防止等级回退（等级只会增长）
        - jin_bi 仅在新值非零时更新，防止 IPC 超时/脚本停止导致零值覆盖
        - login_at 仅在当前为空时填充（COALESCE），防止覆盖已有登录时间
        """
        login_at_val = self._normalize_timestamp_text(login_at) or None
        # 零值保护：level 只增不减，jin_bi 零值不覆盖
        if jin_bi and jin_bi != "0":
            changed = self._conn.execute(
                "UPDATE accounts SET level=MAX(level, ?), jin_bi=?, "
                "last_login_at=COALESCE(last_login_at, ?) "
                "WHERE status='运行中' AND assigned_machine=?",
                (level, jin_bi, login_at_val, machine_name),
            ).rowcount
        else:
            changed = self._conn.execute(
                "UPDATE accounts SET level=MAX(level, ?), "
                "last_login_at=COALESCE(last_login_at, ?) "
                "WHERE status='运行中' AND assigned_machine=?",
                (level, login_at_val, machine_name),
            ).rowcount
        if not changed and current_account:
            # 账号未经 allocate，但 TestDemo 已在使用 → 自动绑定
            # 自动绑定时也应用零值保护
            if jin_bi and jin_bi != "0":
                changed = self._conn.execute(
                    "UPDATE accounts SET level=MAX(level, ?), jin_bi=?, "
                    "status='运行中', assigned_machine=?, "
                    "last_login_at=COALESCE(last_login_at, ?) "
                    "WHERE username=? AND (status='空闲中' "
                    "OR (status='运行中' AND assigned_machine IN ('', ?)))",
                    (level, jin_bi, machine_name, login_at_val, current_account, machine_name),
                ).rowcount
            else:
                changed = self._conn.execute(
                    "UPDATE accounts SET level=MAX(level, ?), "
                    "status='运行中', assigned_machine=?, "
                    "last_login_at=COALESCE(last_login_at, ?) "
                    "WHERE username=? AND (status='空闲中' "
                    "OR (status='运行中' AND assigned_machine IN ('', ?)))",
                    (level, machine_name, login_at_val, current_account, machine_name),
                ).rowcount
        if not changed:
            return
        if state == "已完成":
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._conn.execute(
                "UPDATE accounts SET status='已完成', completed_at=?, "
                "last_login_at=COALESCE(last_login_at, ?) "
                "WHERE status='运行中' AND assigned_machine=?",
                (now, login_at_val, machine_name),
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
            login_at = self._normalize_timestamp_text(acc.get("login_at"))

            existing = self._conn.execute(
                "SELECT id, status, level, jin_bi, last_login_at "
                "FROM accounts WHERE username = ?",
                (username,),
            ).fetchone()

            # 软删除的账号：不允许 slave sync 复活
            if existing is not None and existing["status"] == "已删除":
                continue

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
                    " status, level, jin_bi, completed_at, last_login_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (username, str(acc.get("password", "")),
                     str(acc.get("bind_email", "")),
                     str(acc.get("bind_email_pwd", "")),
                     status, level, jin_bi, now, login_at or None),
                )
                inserted += 1
                continue

            last_login_at = self._resolve_last_login_at(
                existing["last_login_at"],
                login_at,
                is_active=is_active,
            )
            current_login_at = self._normalize_timestamp_text(existing["last_login_at"]) or None

            # 已有账号：封禁检测
            if is_banned and existing["status"] != "已封禁":
                self._conn.execute(
                    "UPDATE accounts SET status='已封禁', last_login_at=? "
                    "WHERE id = ?",
                    (last_login_at, existing["id"]),
                )
                updated += 1
                continue

            # 已完成但 is_active=true → 账号被重新使用，恢复为空闲中
            if is_active and existing["status"] == "已完成":
                self._conn.execute(
                    "UPDATE accounts SET status='空闲中', assigned_machine='', "
                    "completed_at=NULL, level=?, jin_bi=?, last_login_at=? "
                    "WHERE id = ?",
                    (level, jin_bi, last_login_at, existing["id"]),
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
                        "completed_at=?, last_login_at=? WHERE id = ?",
                        (level, jin_bi, now, last_login_at, existing["id"]),
                    )
                    updated += 1
                elif self._update_last_login_at(existing["id"], last_login_at):
                    updated += 1
                continue

            row_changed = (
                int(existing["level"]) != level
                or str(existing["jin_bi"]) != jin_bi
                or last_login_at != current_login_at
            )
            if row_changed:
                self._conn.execute(
                    "UPDATE accounts SET level = ?, jin_bi = ?, last_login_at = ? "
                    "WHERE id = ?",
                    (level, jin_bi, last_login_at, existing["id"]),
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
            "SELECT * FROM accounts WHERE assigned_machine=? AND status='运行中' "
            "ORDER BY id LIMIT 1",
            (machine_name,),
        )
        row = cur.fetchone()
        return self._row_to_info(row) if row else None

    def get_all_accounts(self) -> list[AccountInfo]:
        """全量查询，按 id 排序（排除软删除）"""
        cur = self._conn.execute(
            "SELECT * FROM accounts WHERE status != '已删除' ORDER BY id"
        )
        return [self._row_to_info(r) for r in cur.fetchall()]

    def get_idle_accounts(self) -> list[AccountInfo]:
        """查询所有空闲账号，避免大屏为过滤空闲账号加载全表。"""
        cur = self._conn.execute(
            "SELECT * FROM accounts WHERE status='空闲中' ORDER BY id"
        )
        return [self._row_to_info(r) for r in cur.fetchall()]

    # ── 导出 ───────────────────────────────────────────────

    def get_completed_not_uploaded(self, limit: int | None = None) -> list[AccountInfo]:
        """查询已完成但未上传到平台的账号"""
        sql = (
            "SELECT * FROM accounts WHERE status='已完成' AND uploaded_at IS NULL "
            "ORDER BY id"
        )
        params: tuple[object, ...] = ()
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params = (limit,)
        cur = self._conn.execute(sql, params)
        return [self._row_to_info(r) for r in cur.fetchall()]

    def mark_uploaded(self, usernames: list[str]) -> int:
        """标记账号已上传到平台（批量，事务内）"""
        if not usernames:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        placeholders = ",".join("?" for _ in usernames)
        cur = self._conn.execute(
            "UPDATE accounts SET uploaded_at=? "
            f"WHERE username IN ({placeholders}) "  # noqa: S608
            "AND status='已完成' AND uploaded_at IS NULL",
            (now, *usernames),
        )
        self._conn.commit()
        return cur.rowcount

    def mark_taken_by_platform(self, usernames: list[str]) -> int:
        """平台确认账号已被取号 → 本地状态流转为 '已取号'"""
        if not usernames:
            return 0
        placeholders = ",".join("?" * len(usernames))
        total = self._conn.execute(
            f"UPDATE accounts SET status='已取号' "
            f"WHERE username IN ({placeholders}) AND status='已完成'",
            usernames,
        ).rowcount
        if total:
            self._conn.commit()
            self._refresh_counts()
            self.pool_changed.emit()
        return total

    def export_completed(self, mark_fetched: bool = False) -> str:
        """导出已完成账号，使用提号 10 字段格式（含表头）

        mark_fetched=True 时，导出后将状态流转为 '已取号'。
        """
        cur = self._conn.execute(
            "SELECT * FROM accounts WHERE status='已完成' ORDER BY id",
        )
        lines: list[str] = [EXPORT_ACCOUNT_HEADER]
        for row in cur.fetchall():
            login_time_str = row["last_login_at"] or "无"
            completed_time_str = row["completed_at"] or "无"
            lines.append(
                f"{row['username']}----{row['password']}----{row['bind_email']}----"
                f"{row['bind_email_pwd']}----{row['level']}----{row['jin_bi']}----"
                f"正常----无----{login_time_str}----{completed_time_str}"
            )
        if mark_fetched:
            self._conn.execute("UPDATE accounts SET status='已取号' WHERE status='已完成'")
            self._conn.commit()
            self._refresh_counts()
            self.pool_changed.emit()
        return "\n".join(lines)

    def export_all(self) -> str:
        """导出所有账号（含表头），不改变状态。排除软删除。"""
        cur = self._conn.execute(
            "SELECT * FROM accounts WHERE status != '已删除' ORDER BY id"
        )
        lines: list[str] = [EXPORT_ACCOUNT_HEADER]
        for row in cur.fetchall():
            login_time_str = row["last_login_at"] or "无"
            completed_time_str = row["completed_at"] or "无"
            lines.append(
                f"{row['username']}----{row['password']}----{row['bind_email']}----"
                f"{row['bind_email_pwd']}----{row['level']}----{row['jin_bi']}----"
                f"{row['status']}----{row['notes']}----{login_time_str}----{completed_time_str}"
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
        """按用户名批量软删除（标记为 '已删除'），防止 slave sync 复活。"""
        if not usernames:
            return 0
        placeholders = ",".join("?" * len(usernames))
        cur = self._conn.execute(
            f"UPDATE accounts SET status='已删除', assigned_machine='' "
            f"WHERE username IN ({placeholders}) AND status != '已删除'",  # noqa: S608
            usernames,
        )
        self._conn.commit()
        self._refresh_counts()
        self.pool_changed.emit()
        return cur.rowcount

    def purge_deleted(self) -> int:
        """物理删除所有软删除记录，释放空间。"""
        cur = self._conn.execute("DELETE FROM accounts WHERE status='已删除'")
        self._conn.commit()
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

    @classmethod
    def _get_write_lock(cls, db_path: str) -> threading.RLock:
        with cls._path_locks_guard:
            lock = cls._path_locks.get(db_path)
            if lock is None:
                lock = threading.RLock()
                cls._path_locks[db_path] = lock
            return lock

    @contextlib.contextmanager
    def _write_transaction(self):
        with self._write_lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def _ensure_uploaded_at_column(self) -> None:
        """迁移：为已有数据库添加 uploaded_at 列"""
        cur = self._conn.execute("PRAGMA table_info(accounts)")
        columns = {row[1] for row in cur.fetchall()}
        if "uploaded_at" not in columns:
            self._conn.execute(
                "ALTER TABLE accounts ADD COLUMN uploaded_at TEXT DEFAULT NULL"
            )
            self._conn.commit()

    def _ensure_last_login_at_column(self) -> None:
        """迁移：为已有数据库添加 last_login_at 列"""
        cur = self._conn.execute("PRAGMA table_info(accounts)")
        columns = {row[1] for row in cur.fetchall()}
        if "last_login_at" not in columns:
            self._conn.execute(
                "ALTER TABLE accounts ADD COLUMN last_login_at TEXT DEFAULT NULL"
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
                                 CHECK (status IN ('空闲中', '运行中', '已完成', '已取号', '已封禁', '已删除')),
                assigned_machine TEXT    NOT NULL DEFAULT '',
                level            INTEGER NOT NULL DEFAULT 0,
                jin_bi           TEXT    NOT NULL DEFAULT '0',
                completed_at     TEXT    DEFAULT NULL,
                uploaded_at      TEXT    DEFAULT NULL,
                last_login_at    TEXT    DEFAULT NULL,
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
            CREATE INDEX IF NOT EXISTS idx_username_status ON accounts(username, status);
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
        deleted = counts.pop("已删除", 0)  # noqa: F841
        self._total = sum(counts.values())
        self._available = counts.get("空闲中", 0)
        self._in_use = counts.get("运行中", 0)
        self._completed = counts.get("已完成", 0)

    @staticmethod
    def _normalize_timestamp_text(raw: object) -> str:
        if raw is None:
            return ""
        value = str(raw).strip()
        if not value:
            return ""
        with contextlib.suppress(ValueError):
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
        return ""

    @staticmethod
    def _merge_timestamp_text(existing: object, incoming: str) -> str | None:
        current = AccountDB._normalize_timestamp_text(existing)
        if not current:
            return incoming or None
        if not incoming:
            return current
        return max(current, incoming)

    @staticmethod
    def _resolve_last_login_at(existing: object, incoming: str, *, is_active: bool) -> str | None:
        if is_active and incoming:
            return incoming
        return AccountDB._merge_timestamp_text(existing, incoming)

    def _update_last_login_at(self, account_id: int, last_login_at: str | None) -> bool:
        row = self._conn.execute(
            "SELECT last_login_at FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        current = self._normalize_timestamp_text(row["last_login_at"] if row else None)
        target = self._normalize_timestamp_text(last_login_at)
        if current == target:
            return False
        self._conn.execute(
            "UPDATE accounts SET last_login_at = ? WHERE id = ?",
            (target or None, account_id),
        )
        return True

    @staticmethod
    def _row_to_info(row: sqlite3.Row) -> AccountInfo:
        last_login_at = None
        if row["last_login_at"]:
            with contextlib.suppress(ValueError):
                last_login_at = datetime.strptime(
                    row["last_login_at"], "%Y-%m-%d %H:%M:%S",
                )
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
            last_login_at=last_login_at,
            completed_at=completed_at,
            updated_at=updated_at,
            created_at=created_at,
        )
