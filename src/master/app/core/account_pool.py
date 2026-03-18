"""账号池管理器：加载、分配、回收账号"""
from __future__ import annotations

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

    # ── 加载 ───────────────────────────────────────────────

    def load_from_text(self, text: str) -> None:
        """从文本加载账号，格式: username----password（每行一个）"""
        self.accounts.clear()
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            self.accounts.append(AccountInfo.from_line(line))
        self.pool_changed.emit()

    def load_from_file(self, path: str | Path) -> None:
        """从文件加载账号"""
        text = Path(path).read_text(encoding="utf-8")
        self.load_from_text(text)

    # ── 分配 / 回收 ───────────────────────────────────────

    def allocate(self, machine_name: str) -> AccountInfo | None:
        """分配第一个空闲账号给指定机器，返回 None 表示无可用账号"""
        for acc in self.accounts:
            if acc.status == AccountStatus.IDLE:
                acc.status = AccountStatus.IN_USE
                acc.assigned_machine = machine_name
                self.pool_changed.emit()
                return acc
        return None

    def complete(self, machine_name: str, level: int = 0) -> None:
        """标记机器对应的账号为已完成"""
        for acc in self.accounts:
            if acc.assigned_machine == machine_name and acc.status == AccountStatus.IN_USE:
                acc.status = AccountStatus.COMPLETED
                acc.level = level
                acc.completed_at = datetime.now()
                self.pool_changed.emit()
                return

    def release(self, machine_name: str) -> None:
        """释放机器对应的账号，恢复为空闲"""
        for acc in self.accounts:
            if acc.assigned_machine == machine_name and acc.status == AccountStatus.IN_USE:
                acc.status = AccountStatus.IDLE
                acc.assigned_machine = ""
                self.pool_changed.emit()
                return

    # ── 导出 ───────────────────────────────────────────────

    def export_completed(self) -> str:
        """导出已完成账号为文本，格式: username----password  等级:N"""
        lines = []
        for acc in self.accounts:
            if acc.status == AccountStatus.COMPLETED:
                lines.append(f"{acc.username}----{acc.password}  等级:{acc.level}")
        return "\n".join(lines)

    # ── 统计属性 ───────────────────────────────────────────

    @property
    def total_count(self) -> int:
        return len(self.accounts)

    @property
    def available_count(self) -> int:
        return sum(1 for a in self.accounts if a.status == AccountStatus.IDLE)

    @property
    def in_use_count(self) -> int:
        return sum(1 for a in self.accounts if a.status == AccountStatus.IN_USE)

    @property
    def completed_count(self) -> int:
        return sum(1 for a in self.accounts if a.status == AccountStatus.COMPLETED)
