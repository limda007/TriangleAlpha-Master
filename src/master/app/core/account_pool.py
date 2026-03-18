"""账号池管理器：加载、分配、回收账号"""
from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from common.models import AccountInfo, AccountStatus


class AccountPool(QObject):
    """管理账号列表的分配和生命周期"""

    pool_changed = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.accounts: list[AccountInfo] = []
        # P1: 按状态索引，allocate() O(1)
        self._by_status: dict[AccountStatus, list[AccountInfo]] = {
            s: [] for s in AccountStatus
        }

    # ── 加载 ───────────────────────────────────────────────

    def _rebuild_index(self) -> None:
        """重建状态索引"""
        for s in AccountStatus:
            self._by_status[s] = []
        for acc in self.accounts:
            self._by_status[acc.status].append(acc)

    def load_from_text(self, text: str) -> None:
        """从文本加载账号，格式: username----password（每行一个）"""
        self.accounts.clear()
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            self.accounts.append(AccountInfo.from_line(line))
        self._rebuild_index()
        self.pool_changed.emit()

    def load_from_file(self, path: str | Path) -> None:
        """从文件加载账号"""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            raise OSError(f"无法读取账号文件: {e}") from e
        self.load_from_text(text)

    # ── 分配 / 回收 ───────────────────────────────────────

    def allocate(self, machine_name: str) -> AccountInfo | None:
        """分配第一个空闲账号给指定机器，返回 None 表示无可用账号"""
        idle_list = self._by_status[AccountStatus.IDLE]
        if not idle_list:
            return None
        acc = idle_list.pop(0)
        acc.status = AccountStatus.IN_USE
        acc.assigned_machine = machine_name
        self._by_status[AccountStatus.IN_USE].append(acc)
        self.pool_changed.emit()
        return acc

    def _move_status(self, acc: AccountInfo, old: AccountStatus, new: AccountStatus) -> None:
        """从旧状态索引移到新状态索引"""
        with contextlib.suppress(ValueError):
            self._by_status[old].remove(acc)
        acc.status = new
        self._by_status[new].append(acc)

    def complete(self, machine_name: str, level: int = 0) -> None:
        """标记机器对应的账号为已完成"""
        target = next(
            (a for a in self._by_status[AccountStatus.IN_USE] if a.assigned_machine == machine_name),
            None,
        )
        if target is not None:
            self._move_status(target, AccountStatus.IN_USE, AccountStatus.COMPLETED)
            target.level = level
            target.completed_at = datetime.now()
            self.pool_changed.emit()

    def release(self, machine_name: str) -> None:
        """释放机器对应的账号，恢复为空闲"""
        target = next(
            (a for a in self._by_status[AccountStatus.IN_USE] if a.assigned_machine == machine_name),
            None,
        )
        if target is not None:
            self._move_status(target, AccountStatus.IN_USE, AccountStatus.IDLE)
            target.assigned_machine = ""
            self.pool_changed.emit()

    # ── 导出 ───────────────────────────────────────────────

    def export_completed(self) -> str:
        """导出已完成账号，对齐原版 9 字段格式:
        账号----密码----邮箱----邮箱密码----等级----金币----状态----登录时间----登出时间
        """
        lines = []
        for acc in self.accounts:
            if acc.status == AccountStatus.COMPLETED:
                time_str = acc.completed_at.strftime("%Y-%m-%d %H:%M:%S") if acc.completed_at else "无"
                lines.append(
                    f"{acc.username}----{acc.password}----{acc.bind_email}----"
                    f"{acc.bind_email_password}----{acc.level}----{acc.jin_bi}----"
                    f"正常----无----{time_str}"
                )
        return "\n".join(lines)

    # ── 统计属性 ───────────────────────────────────────────

    @property
    def total_count(self) -> int:
        return len(self.accounts)

    @property
    def available_count(self) -> int:
        return len(self._by_status[AccountStatus.IDLE])

    @property
    def in_use_count(self) -> int:
        return len(self._by_status[AccountStatus.IN_USE])

    @property
    def completed_count(self) -> int:
        return len(self._by_status[AccountStatus.COMPLETED])
