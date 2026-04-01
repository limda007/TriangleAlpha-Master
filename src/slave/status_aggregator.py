"""运行时状态聚合 — IPC → 缓存 → 文件三级数据融合。"""
from __future__ import annotations

import time
from datetime import datetime

from common.protocol import IPC_TIMEOUT, GameState
from slave.ipc_receiver import LocalIpcReceiver
from slave.logging_utils import get_logger
from slave.models import RuntimeStatus
from slave.state_store import SlaveStateStore

logger = get_logger(__name__)

_NEED_ACCOUNT_RETRY_SEC = 15.0
_NEED_ACCOUNT_STATUS_KEYWORDS = ("本地无可用账号", "向中控申请", "申请账号", "请求账号")


class StatusAggregator:
    """聚合 IPC / 文件 / accounts.json 数据。

    不持有 heartbeat 引用，仅负责数据聚合和 NEED_ACCOUNT 冷却追踪。
    """

    def __init__(
        self,
        state_store: SlaveStateStore,
        ipc: LocalIpcReceiver,
    ) -> None:
        self._state_store = state_store
        self._ipc = ipc
        self._last_ipc_jin_bi: str = "0"
        self._last_need_account_request_at: float = 0.0
        self._script_started_at: float | None = None

    @property
    def last_ipc_jin_bi(self) -> str:
        return self._last_ipc_jin_bi

    @last_ipc_jin_bi.setter
    def last_ipc_jin_bi(self, value: str) -> None:
        self._last_ipc_jin_bi = value

    @property
    def last_need_account_request_at(self) -> float:
        return self._last_need_account_request_at

    @last_need_account_request_at.setter
    def last_need_account_request_at(self, value: float) -> None:
        self._last_need_account_request_at = value

    def set_script_started_at(self, ts: float | None) -> None:
        self._script_started_at = ts

    def reset_ipc_state(self) -> None:
        """账号更新时重置 IPC 缓存状态。"""
        self._last_ipc_jin_bi = "0"
        self._last_need_account_request_at = 0.0
        self._ipc.clear_snapshot()

    def _compute_persisted_elapsed(self) -> str:
        """从持久化的 last_login_at 推算上机时间（秒），跨重启不丢失。"""
        login_at_str = self._state_store.get_active_login_at()
        if not login_at_str:
            return ""
        try:
            login_at = datetime.strptime(login_at_str, "%Y-%m-%d %H:%M:%S")
            elapsed_sec = max(0, int((datetime.now() - login_at).total_seconds()))
            return str(elapsed_sec)
        except ValueError:
            return ""

    def load_runtime_snapshot(self) -> RuntimeStatus:
        """三级数据聚合：IPC 优先 → IPC 缓存沿用 → 文件兜底。"""
        persisted_elapsed = self._compute_persisted_elapsed()
        if persisted_elapsed:
            default_elapsed = persisted_elapsed
        elif self._script_started_at is not None:
            default_elapsed = str(max(0, int(time.time() - self._script_started_at)))
        else:
            default_elapsed = "0"

        active_acc = self._state_store.load_active_account(default_elapsed=default_elapsed)
        active_name = active_acc.current_account if active_acc else ""
        active_level = active_acc.level if active_acc else 0
        active_jin_bi = active_acc.jin_bi if active_acc else "0"

        # ── IPC 优先：从 TestDemo 本地 UDP 推送获取实时数据 ──
        ipc_data, ipc_age = self._ipc.snapshot()
        if ipc_data is not None and ipc_age < IPC_TIMEOUT:
            self._last_ipc_jin_bi = ipc_data.jinbi
            raw_status = ipc_data.status_text
            level_raw = ipc_data.level
            return RuntimeStatus(
                state=map_ipc_status(raw_status),
                level=int(level_raw) if level_raw.isdigit() else 0,
                jin_bi=self._last_ipc_jin_bi,
                current_account=active_name,
                elapsed=_max_elapsed(ipc_data.elapsed, default_elapsed) or default_elapsed,
                status_text=raw_status,
            )

        # ── IPC 刚超时但有缓存：沿用最后 IPC 数据，避免文件回退导致金币跳变 ──
        if ipc_data is not None and self._last_ipc_jin_bi != "0":
            raw_status = ipc_data.status_text
            level_raw = ipc_data.level
            return RuntimeStatus(
                state=map_ipc_status(raw_status),
                level=int(level_raw) if level_raw.isdigit() else 0,
                jin_bi=self._last_ipc_jin_bi,
                current_account=active_name,
                elapsed=_max_elapsed(ipc_data.elapsed, default_elapsed) or default_elapsed,
                status_text=raw_status,
            )

        # ── 文件兜底：仅读取数值状态，不再信任磁盘里的 current_account ──
        snapshot = self._state_store.load_runtime_status(default_elapsed=default_elapsed)
        snapshot = RuntimeStatus(
            state=snapshot.state,
            level=snapshot.level or active_level,
            jin_bi=snapshot.jin_bi if snapshot.jin_bi != "0" else active_jin_bi,
            current_account=active_name,
            elapsed=snapshot.elapsed,
            status_text=snapshot.status_text,
        )
        if snapshot.state == GameState.SCRIPT_STOPPED:
            return RuntimeStatus(
                state=GameState.RUNNING,
                level=snapshot.level,
                jin_bi=snapshot.jin_bi,
                current_account=snapshot.current_account,
                elapsed=snapshot.elapsed,
            )
        return snapshot

    def retry_need_account_if_needed(
        self,
        snapshot: RuntimeStatus,
        send_fn: object,
        *,
        now_monotonic: float | None = None,
    ) -> bool:
        """检查并在冷却期后补发 NEED_ACCOUNT。

        send_fn: 可调用对象，实际发送 NEED_ACCOUNT 请求。
        """
        if not is_waiting_for_account(snapshot):
            self._last_need_account_request_at = 0.0
            return False
        now = time.monotonic() if now_monotonic is None else now_monotonic
        if (
            self._last_need_account_request_at > 0
            and now - self._last_need_account_request_at < _NEED_ACCOUNT_RETRY_SEC
        ):
            return False
        send_fn()  # type: ignore[operator]
        self._last_need_account_request_at = now
        logger.info("检测到等待账号状态，补发 NEED_ACCOUNT")
        return True


def map_ipc_status(text: str) -> str:
    """将 TestDemo IPC 上报的状态文字映射为 GameState 值。"""
    if not text:
        return GameState.RUNNING
    if text == "已完成":
        return GameState.COMPLETED
    if "停" in text or "退出" in text:
        return GameState.SCRIPT_STOPPED
    return GameState.RUNNING


def _max_elapsed(a: str, b: str) -> str:
    """取两个 elapsed 秒数字符串中的较大值。"""
    a_val = int(a) if a.isdigit() else 0
    b_val = int(b) if b.isdigit() else 0
    best = max(a_val, b_val)
    return str(best) if best > 0 else ""


def is_waiting_for_account(snapshot: RuntimeStatus) -> bool:
    """检测快照是否处于"等待账号"状态。"""
    if snapshot.state != GameState.RUNNING:
        return False
    if snapshot.current_account.strip():
        return False
    status_text = snapshot.status_text.strip()
    if not status_text:
        return False
    return any(keyword in status_text for keyword in _NEED_ACCOUNT_STATUS_KEYWORDS)
