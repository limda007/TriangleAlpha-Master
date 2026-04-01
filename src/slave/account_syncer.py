"""账号同步 — 定期将本地账号快照同步给 master，并校正活跃账号的登录时间。"""
from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from datetime import datetime, timedelta

from slave.logging_utils import get_logger
from slave.models import RuntimeStatus
from slave.state_store import SlaveStateStore

logger = get_logger(__name__)


class AccountSyncer:
    """构建账号快照并校正活跃账号 login_at。"""

    def __init__(self, state_store: SlaveStateStore) -> None:
        self._state_store = state_store

    def build_sync_accounts(
        self,
        load_snapshot: Callable[[], RuntimeStatus],
        now: datetime | None = None,
    ) -> list[dict[str, object]]:
        """构建待同步账号快照，并用运行时长校正当前活跃账号的登录时间。"""
        accounts = self._state_store.load_all_game_accounts()
        if not accounts:
            return accounts
        snapshot = load_snapshot()
        align_active_account_login_at(accounts, snapshot, now=now)
        return accounts


def align_active_account_login_at(
    accounts: list[dict[str, object]],
    snapshot: RuntimeStatus,
    *,
    now: datetime | None = None,
) -> None:
    """校正当前活跃账号的 login_at 字段。"""
    current_account = snapshot.current_account.strip()
    if not current_account:
        return
    login_at = derive_login_at(snapshot.elapsed, now=now)
    if not login_at:
        return
    for account in accounts:
        username = str(account.get("username", "")).strip()
        if username != current_account:
            continue
        account["login_at"] = login_at
        return


def derive_login_at(elapsed: object, *, now: datetime | None = None) -> str | None:
    """根据运行时长推算登录时间。"""
    elapsed_seconds = parse_elapsed_seconds(elapsed)
    if elapsed_seconds is None:
        return None
    current_time = now or datetime.now()
    return (current_time - timedelta(seconds=elapsed_seconds)).strftime("%Y-%m-%d %H:%M:%S")


def parse_elapsed_seconds(raw: object) -> int | None:
    """解析运行时长字符串为秒数，支持纯数字和 '4h56m30s' 格式。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    with contextlib.suppress(ValueError):
        return max(0, int(text))
    match = re.fullmatch(r"(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?", text)
    if not match or not match.group(0):
        return None
    hours = int(match.group("hours") or "0")
    minutes = int(match.group("minutes") or "0")
    seconds = int(match.group("seconds") or "0")
    return hours * 3600 + minutes * 60 + seconds
