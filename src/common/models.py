"""共享数据模型"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class AccountStatus(enum.Enum):
    IDLE = "空闲中"
    IN_USE = "运行中"
    COMPLETED = "已完成"
    FETCHED = "已取号"
    BANNED = "已封禁"


@dataclass
class NodeInfo:
    machine_name: str
    ip: str
    user_name: str = ""
    status: str = "在线"
    level: int = 0
    jin_bi: str = "0"
    elapsed: str = "0"  # 运行时间
    current_account: str = ""
    group: str = "默认"
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    slave_version: str = ""
    teammate_fill: str = ""
    weapon_config: str = ""
    level_threshold: str = ""
    loot_count: str = ""
    token_key: str = ""    # slave 端 token.txt 内容
    last_seen: datetime = field(default_factory=datetime.now)
    last_status_update: datetime = field(default_factory=datetime.now)
    game_state: str = ""  # TestDemo 上报的游戏状态（运行中/已完成/脚本已停止）

    def is_online(self, timeout_sec: int = 15) -> bool:
        return (datetime.now() - self.last_seen).total_seconds() < timeout_sec


@dataclass
class AccountInfo:
    """账号信息 — 对齐原版 TestDemo.exe 的 AccountInfo 结构

    accounts.txt 格式: Steam账号----密码----邮箱----邮箱密码----[来源/备注]
    至少 4 个字段，第 5 个字段（来源/备注）可选。
    """

    username: str
    password: str = ""
    bind_email: str = ""
    bind_email_password: str = ""
    notes: str = ""
    status: AccountStatus = AccountStatus.IDLE
    assigned_machine: str = ""
    level: int = 0
    jin_bi: str = "0"
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    created_at: datetime | None = None

    @classmethod
    def from_line(cls, line: str) -> AccountInfo:
        """解析 ---- 分隔的账号行，兼容 2~5 字段"""
        parts = line.strip().split("----")
        if not parts:
            return cls(username="")
        username = parts[0].strip()
        password = parts[1].strip() if len(parts) > 1 else ""
        bind_email = parts[2].strip() if len(parts) > 2 else ""
        bind_email_password = parts[3].strip() if len(parts) > 3 else ""
        notes = parts[4].strip() if len(parts) > 4 else ""
        return cls(
            username=username,
            password=password,
            bind_email=bind_email,
            bind_email_password=bind_email_password,
            notes=notes,
        )

    def to_line(self) -> str:
        """序列化为 ---- 分隔行（不含运行时字段）"""
        parts = [self.username, self.password, self.bind_email, self.bind_email_password]
        if self.notes:
            parts.append(self.notes)
        return "----".join(parts)

    @property
    def masked_password(self) -> str:
        return "••••••••" if self.password else ""


@dataclass
class OperationRecord:
    timestamp: datetime
    op_type: str
    target: str
    detail: str = ""
    result: str = ""
