"""共享数据模型"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class AccountStatus(enum.Enum):
    IDLE = "空闲"
    IN_USE = "使用中"
    COMPLETED = "已完成"


@dataclass
class NodeInfo:
    machine_name: str
    ip: str
    user_name: str = ""
    status: str = "在线"
    level: int = 0
    jin_bi: str = "0"
    current_account: str = ""
    group: str = "默认"
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    slave_version: str = ""
    last_seen: datetime = field(default_factory=datetime.now)
    last_status_update: datetime = field(default_factory=datetime.now)

    def is_online(self, timeout_sec: int = 15) -> bool:
        return (datetime.now() - self.last_seen).total_seconds() < timeout_sec


@dataclass
class AccountInfo:
    username: str
    password: str = ""
    status: AccountStatus = AccountStatus.IDLE
    assigned_machine: str = ""
    level: int = 0
    completed_at: datetime | None = None

    @classmethod
    def from_line(cls, line: str) -> AccountInfo:
        parts = line.strip().split("----", maxsplit=1)
        username = parts[0].strip()
        password = parts[1].strip() if len(parts) > 1 else ""
        return cls(username=username, password=password)

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
