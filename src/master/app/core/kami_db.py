"""卡密 SQLite 持久化管理器"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from common.models import KamiInfo, KamiStatus

logger = logging.getLogger(__name__)

_KAMI_SCHEMA = """\
CREATE TABLE IF NOT EXISTS kamis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kami_code        TEXT    NOT NULL UNIQUE,
    kami_type        TEXT    NOT NULL DEFAULT '',
    end_date         TEXT    NOT NULL DEFAULT '',
    remaining_days   INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL DEFAULT '未知'
                     CHECK (status IN ('已激活', '已过期', '未使用', '未知')),
    device_used      INTEGER NOT NULL DEFAULT 0,
    device_total     INTEGER NOT NULL DEFAULT 0,
    activated_at     TEXT    DEFAULT NULL,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_kami_status ON kamis(status);
CREATE INDEX IF NOT EXISTS idx_kami_code ON kamis(kami_code);

CREATE TABLE IF NOT EXISTS kami_bindings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    kami_id   INTEGER NOT NULL,
    node_name TEXT    NOT NULL,
    bound_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(kami_id, node_name),
    FOREIGN KEY (kami_id) REFERENCES kamis(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_binding_kami ON kami_bindings(kami_id);

CREATE TRIGGER IF NOT EXISTS trg_kami_updated AFTER UPDATE ON kamis
BEGIN
    UPDATE kamis SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;
"""

# API 返回的 status: "已激活" / "未使用"，ok=false 时标记为已过期
_STATUS_FROM_API = {
    True: "已激活",
    False: "已过期",
}
_ALLOCATABLE_STATUSES = frozenset({
    KamiStatus.ACTIVATED.value,
    KamiStatus.UNUSED.value,
})


def _is_allocatable_status(status: str) -> bool:
    """判断卡密是否处于可分配状态。"""
    return status in _ALLOCATABLE_STATUSES


def _parse_device_count(val: str | int) -> tuple[int, int]:
    """解析 device_count 字段，如 '0/1' → (0, 1)

    缺失或格式异常时返回 (0, 0)，调用方应对 device_total=0 做后备处理。
    """
    if isinstance(val, int):
        # 直接给了一个整数 → 视为 total
        return (0, max(0, val)) if val > 0 else (0, 0)
    if isinstance(val, str):
        if "/" in val:
            parts = val.split("/", 1)
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                return 0, 0
        # 纯数字字符串 → 视为 total
        stripped = val.strip()
        if stripped.isdigit():
            v = int(stripped)
            return (0, v) if v > 0 else (0, 0)
    return 0, 0


def _infer_activated_at(end_date: str, remaining_days: int) -> str:
    """通过 end_date - remaining_days 推断激活日期"""
    if not end_date or end_date == "9999-12-31":
        return ""
    try:
        end = datetime.strptime(end_date, "%Y-%m-%d")
        activated = end - timedelta(days=remaining_days)
        return activated.strftime("%Y-%m-%d")
    except ValueError:
        return ""


class KamiDB(QObject):
    """卡密 SQLite 持久化层 — 操作 accounts.db 中的 kamis + kami_bindings 表"""

    kami_changed = pyqtSignal()

    def __init__(
        self,
        db_path: str | Path,
        parent: QObject | None = None,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        super().__init__(parent)
        self._db_path = str(db_path)
        self._owns_conn = conn is None
        if conn is not None:
            self._conn = conn
        else:
            self._conn = sqlite3.connect(self._db_path, timeout=10)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_KAMI_SCHEMA)
        self._conn.commit()
        # 迁移：修正历史脏数据
        self._migrate_status_aliases()
        # 缓存计数
        self._total = 0
        self._activated = 0
        self._expired = 0
        self._unused = 0
        self._refresh_counts()

    def close(self) -> None:
        if self._owns_conn:
            with contextlib.suppress(Exception):
                self._conn.close()

    # ── 导入 / 更新 ──────────────────────────────────────

    def upsert_kamis(self, results: list[dict]) -> tuple[int, int]:
        """从 API 响应 upsert 卡密，返回 (inserted, updated)"""
        inserted = updated = 0
        for r in results:
            code = r.get("kami", "")
            if not code:
                continue
            ok = r.get("ok", False)
            # 优先使用 API 返回的 status 字段，fallback 到 ok 映射
            raw_status = r.get("status", _STATUS_FROM_API.get(ok, "未知"))
            # 确保 status 在 DB 合法值范围内
            status = raw_status if raw_status in KamiStatus._value2member_map_ else _STATUS_FROM_API.get(ok, "未知")
            kami_type = r.get("kami_type", "")
            end_date = r.get("end_date", "")
            remaining = r.get("remaining_days", 0)
            # 解析 device_count 字段（如 "0/1"）
            device_used, device_total = _parse_device_count(
                r.get("device_count", "0/0"),
            )
            # 有效卡密 device_total 不应为 0，后备为 1
            if _is_allocatable_status(status) and device_total == 0:
                device_total = 1
            # 推断激活日期
            activated_at = _infer_activated_at(end_date, remaining)
            cur = self._conn.execute(
                "INSERT INTO kamis (kami_code, kami_type, end_date, remaining_days,"
                " status, device_used, device_total, activated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(kami_code) DO UPDATE SET"
                " kami_type=excluded.kami_type, end_date=excluded.end_date,"
                " remaining_days=excluded.remaining_days, status=excluded.status,"
                " device_used=excluded.device_used, device_total=excluded.device_total,"
                " activated_at=excluded.activated_at",
                (code, kami_type, end_date, remaining, status,
                 device_used, device_total, activated_at),
            )
            if cur.rowcount > 0:
                # 判断是 insert 还是 update
                existing = self._conn.execute(
                    "SELECT created_at, updated_at FROM kamis WHERE kami_code=?",
                    (code,),
                ).fetchone()
                if existing and existing["created_at"] == existing["updated_at"]:
                    inserted += 1
                else:
                    updated += 1
        self._conn.commit()
        self._refresh_counts()
        self.kami_changed.emit()
        return inserted, updated

    # ── 查询 ─────────────────────────────────────────────

    def get_all_kamis(self) -> list[KamiInfo]:
        """全量查询 + 聚合绑定节点"""
        cur = self._conn.execute(
            "SELECT k.*, GROUP_CONCAT(b.node_name, ', ') AS nodes"
            " FROM kamis k LEFT JOIN kami_bindings b ON k.id = b.kami_id"
            " GROUP BY k.id ORDER BY k.id",
        )
        return [self._row_to_info(r) for r in cur.fetchall()]

    def get_kami_codes(self) -> list[str]:
        """返回所有卡密代码"""
        cur = self._conn.execute("SELECT kami_code FROM kamis")
        return [r["kami_code"] for r in cur.fetchall()]

    def find_available_kami(self) -> KamiInfo | None:
        """找到一个可分配的卡密：有效状态且仍有剩余额度。"""
        cur = self._conn.execute(
            "SELECT k.*, GROUP_CONCAT(b.node_name, ', ') AS nodes"
            " FROM kamis k LEFT JOIN kami_bindings b ON k.id = b.kami_id"
            " WHERE k.status IN (?, ?) AND k.device_used < k.device_total"
            " GROUP BY k.id ORDER BY k.remaining_days DESC LIMIT 1",
            tuple(_ALLOCATABLE_STATUSES),
        )
        row = cur.fetchone()
        if row:
            logger.info("[卡密查找] 找到可用卡密: id=%d, code=%s…, 剩余=%d天, 设备=%d/%d",
                        row["id"], row["kami_code"][:8], row["remaining_days"],
                        row["device_used"], row["device_total"])
        else:
            logger.warning("[卡密查找] 没有可用卡密（状态=%s, 且仍有额度）", list(_ALLOCATABLE_STATUSES))
        return self._row_to_info(row) if row else None

    # ── 绑定 / 解绑 ──────────────────────────────────────

    def bind_node(self, kami_id: int, node_name: str) -> bool:
        """绑定卡密到节点，返回是否成功"""
        existing = self._conn.execute(
            "SELECT kami_id FROM kami_bindings WHERE node_name=? LIMIT 1",
            (node_name,),
        ).fetchone()
        if existing:
            same = int(existing["kami_id"]) == kami_id
            logger.info("[卡密绑定] %s 已绑定 kami_id=%d (相同=%s)", node_name, int(existing["kami_id"]), same)
            return same

        kami_row = self._conn.execute(
            "SELECT status, device_used, device_total FROM kamis WHERE id=? LIMIT 1",
            (kami_id,),
        ).fetchone()
        if kami_row is None:
            logger.error("[卡密绑定] kami_id=%d 不存在", kami_id)
            return False
        if not _is_allocatable_status(str(kami_row["status"])):
            logger.warning("[卡密绑定] kami_id=%d 状态不可分配: %s", kami_id, kami_row["status"])
            return False
        if int(kami_row["device_total"]) <= int(kami_row["device_used"]):
            logger.warning("[卡密绑定] kami_id=%d 设备额度已满: %d/%d", kami_id, int(kami_row["device_used"]), int(kami_row["device_total"]))
            return False

        try:
            self._conn.execute(
                "INSERT INTO kami_bindings (kami_id, node_name) VALUES (?, ?)",
                (kami_id, node_name),
            )
            # 更新 device_used + activated_at
            self._conn.execute(
                "UPDATE kamis SET device_used = device_used + 1,"
                " activated_at = COALESCE(activated_at, datetime('now','localtime'))"
                " WHERE id = ?",
                (kami_id,),
            )
            self._conn.commit()
            self._refresh_counts()
            self.kami_changed.emit()
            return True
        except sqlite3.IntegrityError:
            return False  # 已绑定

    def get_kami_for_node(self, node_name: str) -> KamiInfo | None:
        """查询某节点绑定的卡密（取第一条）"""
        cur = self._conn.execute(
            "SELECT k.*, GROUP_CONCAT(b2.node_name, ', ') AS nodes"
            " FROM kami_bindings b"
            " JOIN kamis k ON k.id = b.kami_id"
            " LEFT JOIN kami_bindings b2 ON k.id = b2.kami_id"
            " WHERE b.node_name=?"
            " GROUP BY k.id LIMIT 1",
            (node_name,),
        )
        row = cur.fetchone()
        return self._row_to_info(row) if row else None

    def unbind_node(self, kami_id: int, node_name: str) -> None:
        """解绑卡密与节点"""
        deleted = self._conn.execute(
            "DELETE FROM kami_bindings WHERE kami_id=? AND node_name=?",
            (kami_id, node_name),
        ).rowcount
        if deleted:
            self._conn.execute(
                "UPDATE kamis SET device_used = MAX(0, device_used - 1) WHERE id=?",
                (kami_id,),
            )
            self._conn.commit()
            self._refresh_counts()
            self.kami_changed.emit()

    # ── 删除 ─────────────────────────────────────────────

    def delete_kami(self, kami_id: int) -> None:
        """删除卡密 + 级联删除 bindings"""
        self._conn.execute("DELETE FROM kami_bindings WHERE kami_id=?", (kami_id,))
        self._conn.execute("DELETE FROM kamis WHERE id=?", (kami_id,))
        self._conn.commit()
        self._refresh_counts()
        self.kami_changed.emit()

    def delete_kamis(self, kami_ids: list[int]) -> int:
        """批量删除"""
        count = 0
        for kid in kami_ids:
            self._conn.execute("DELETE FROM kami_bindings WHERE kami_id=?", (kid,))
            count += self._conn.execute(
                "DELETE FROM kamis WHERE id=?", (kid,),
            ).rowcount
        if count:
            self._conn.commit()
            self._refresh_counts()
            self.kami_changed.emit()
        return count

    # ── 统计属性 ──────────────────────────────────────────

    @property
    def total_count(self) -> int:
        return self._total

    @property
    def valid_count(self) -> int:
        return self._activated

    @property
    def expired_count(self) -> int:
        return self._expired

    @property
    def unused_count(self) -> int:
        return self._unused

    # ── 内部方法 ──────────────────────────────────────────

    def _migrate_status_aliases(self) -> None:
        """迁移：修复有效卡密 device_total=0 的历史脏数据。"""
        changed = self._conn.execute(
            "UPDATE kamis SET device_total=1 "
            "WHERE status IN (?, ?) AND device_total=0",
            tuple(_ALLOCATABLE_STATUSES),
        ).rowcount
        if changed:
            self._conn.commit()

    def _refresh_counts(self) -> None:
        cur = self._conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM kamis GROUP BY status",
        )
        counts = {r["status"]: r["cnt"] for r in cur.fetchall()}
        self._total = sum(counts.values())
        self._activated = counts.get("已激活", 0)
        self._expired = counts.get("已过期", 0)
        self._unused = counts.get("未使用", 0)

    @staticmethod
    def _row_to_info(row: sqlite3.Row) -> KamiInfo:
        nodes_str = row["nodes"] if row["nodes"] else ""
        bound_nodes = [n.strip() for n in nodes_str.split(",") if n.strip()]
        try:
            status = KamiStatus(row["status"])
        except ValueError:
            status = KamiStatus.UNKNOWN
        return KamiInfo(
            id=row["id"],
            kami_code=row["kami_code"],
            kami_type=row["kami_type"],
            end_date=row["end_date"],
            remaining_days=row["remaining_days"],
            status=status,
            device_used=row["device_used"],
            device_total=row["device_total"],
            activated_at=row["activated_at"] or "",
            created_at=row["created_at"] or "",
            bound_nodes=bound_nodes,
        )
