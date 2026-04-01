"""共享数据模型"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime

PLATFORM_ACCOUNT_HEADER = "账号----密码----邮箱----邮箱密码----等级----金币----状态----备注----上机时间----完成时间"
EXPORT_ACCOUNT_HEADER = PLATFORM_ACCOUNT_HEADER


class AccountStatus(enum.Enum):
    IDLE = "空闲中"
    IN_USE = "运行中"
    COMPLETED = "已完成"
    FETCHED = "已取号"
    BANNED = "已封禁"
    DELETED = "已删除"


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
    kami_code: str = ""    # slave 端 kamis.txt 当前值
    vram_used_mb: int = 0   # GPU 显存已用 (MB)
    vram_total_mb: int = 0  # GPU 显存总量 (MB)
    status_text: str = ""  # IPC 原始状态文本（如 "等待匹配"/"正在对局"）
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
    last_login_at: datetime | None = None
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

    def to_platform_line(self) -> str:
        """序列化为平台上传格式。"""
        status_text = "封禁" if self.status == AccountStatus.BANNED else "正常"
        notes = self.notes or "无"
        login_time = self.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_login_at else "无"
        completed = self.completed_at.strftime("%Y-%m-%d %H:%M:%S") if self.completed_at else "无"
        parts = [
            self.username, self.password, self.bind_email, self.bind_email_password,
            str(self.level), self.jin_bi, status_text, notes, login_time, completed,
        ]
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


class KamiStatus(enum.Enum):
    ACTIVATED = "已激活"
    UNUSED = "未使用"
    EXPIRED = "已过期"
    UNKNOWN = "未知"


@dataclass
class KamiInfo:
    """卡密信息"""
    id: int = 0
    kami_code: str = ""
    kami_type: str = ""          # "online" / "offline"
    end_date: str = ""           # "YYYY-MM-DD"
    remaining_days: int = 0
    status: KamiStatus = KamiStatus.UNKNOWN
    device_used: int = 0
    device_total: int = 0
    activated_at: str = ""
    created_at: str = ""
    bound_nodes: list[str] = field(default_factory=list)
