"""Slave 侧 Pydantic 数据模型。

Phase 1: 替换散落的 dataclass 和原始 dict，统一数据验证。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from common.protocol import GameState

_DEFAULT_GROUP = "默认"


class SlaveSettings(BaseModel):
    """Slave 本地配置（分组等）。"""

    model_config = ConfigDict(frozen=True)

    group: str = _DEFAULT_GROUP


class RuntimeStatus(BaseModel):
    """运行时快照 — IPC / 文件 / accounts.json 聚合结果。"""

    model_config = ConfigDict(frozen=True)

    state: str = GameState.RUNNING
    level: int = 0
    jin_bi: str = "0"
    current_account: str = ""
    elapsed: str = "0"
    status_text: str = ""


class IpcData(BaseModel):
    """TestDemo 本地 UDP IPC 解析结果。"""

    model_config = ConfigDict(frozen=True)

    level: str = "0"
    jinbi: str = "0"
    status_text: str = ""
    elapsed: str = "0"
