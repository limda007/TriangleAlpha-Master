"""Slave 本地配置与运行状态持久化。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import cast

from common.protocol import GameState
from slave.logging_utils import get_logger
from slave.models import RuntimeStatus, SlaveSettings

logger = get_logger(__name__)
_DEFAULT_GROUP = "默认"


class SlaveStateStore:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)

    @property
    def settings_path(self) -> Path:
        return self._base_dir / "slave_config.json"

    @property
    def runtime_status_path(self) -> Path:
        return self._base_dir / "runtime_status.json"

    @property
    def account_login_state_path(self) -> Path:
        return self._base_dir / "account_login_state.json"

    def load_settings(self) -> SlaveSettings:
        data = self._read_json(self.settings_path)
        if not isinstance(data, dict):
            return SlaveSettings()
        return SlaveSettings(group=self._as_text(data.get("group"), _DEFAULT_GROUP))

    def save_settings(self, settings: SlaveSettings) -> None:
        self.settings_path.write_text(
            json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def save_group(self, group: str) -> None:
        self.save_settings(SlaveSettings(group=self._as_text(group, _DEFAULT_GROUP)))

    def load_runtime_status(self, default_elapsed: str = "0") -> RuntimeStatus:
        data = self._read_json(self.runtime_status_path)
        if not isinstance(data, dict):
            return RuntimeStatus(elapsed=default_elapsed)
        current_account = self._as_text(
            data.get("current_account") or data.get("desc") or data.get("account"),
            "",
        )
        return RuntimeStatus(
            state=self._as_text(data.get("state"), GameState.RUNNING),
            level=self._as_int(data.get("level"), 0),
            jin_bi=self._as_text(
                data.get("jin_bi") or data.get("jinbi") or data.get("JinBi") or data.get("CurrentJinBi"),
                "0",
            ),
            current_account=current_account,
            elapsed=self._as_text(data.get("elapsed"), default_elapsed),
        )

    def load_active_account(self, default_elapsed: str = "0") -> RuntimeStatus | None:
        """从 TestDemo 的 accounts.json 读取当前活跃账号信息。"""
        data = self._read_json(self._base_dir / "accounts.json")
        if not isinstance(data, list):
            return None
        for acc in data:
            if not isinstance(acc, dict):
                continue
            if acc.get("IsActive"):
                return RuntimeStatus(
                    state=GameState.RUNNING,
                    level=self._as_int(acc.get("CurrentLevel"), 0),
                    jin_bi=self._as_text(
                        acc.get("CurrentJinBi") or acc.get("JinBi") or acc.get("jinbi"),
                        "0",
                    ),
                    current_account=self._as_text(acc.get("Username"), ""),
                    elapsed=default_elapsed,
                )
        return None

    def load_all_game_accounts(self) -> list[dict[str, object]]:
        """读取 TestDemo 的 accounts.json 全量账号列表，映射为统一格式。"""
        data = self._read_json(self._base_dir / "accounts.json")
        if not isinstance(data, list):
            return []
        login_state = self._load_account_login_state()
        seen_usernames: set[str] = set()
        state_changed = False
        result: list[dict[str, object]] = []
        for acc in data:
            if not isinstance(acc, dict):
                continue
            username = self._as_text(acc.get("Username"), "")
            if not username:
                continue
            seen_usernames.add(username)
            is_active = bool(acc.get("IsActive"))
            last_login_at, changed = self._touch_login_state(
                login_state, username, is_active,
            )
            state_changed = state_changed or changed
            result.append({
                "username": username,
                "password": self._as_text(acc.get("Password"), ""),
                "bind_email": self._as_text(acc.get("BindEmail"), ""),
                "bind_email_pwd": self._as_text(acc.get("BindEmailPassword"), ""),
                "level": self._as_int(acc.get("CurrentLevel"), 0),
                "jin_bi": self._as_text(
                    acc.get("CurrentJinBi") or acc.get("JinBi") or acc.get("jinbi"),
                    "0",
                ),
                "is_banned": bool(acc.get("IsBanned")),
                "is_active": is_active,
                "login_at": last_login_at,
            })
        for username, state in login_state.items():
            if username in seen_usernames:
                continue
            if bool(state.get("was_active")):
                state["was_active"] = False
                state_changed = True
        if state_changed:
            self._save_account_login_state(login_state)
        return result

    def get_active_login_at(self) -> str:
        """获取当前活跃账号的登录时间戳。

        优先使用 TestDemo 的 LastLoginTime（实际游戏登录时刻），
        回退到 account_login_state.json 的 last_login_at（slave 分配时刻）。
        """
        accounts = self._read_json(self._base_dir / "accounts.json")
        if not isinstance(accounts, list):
            return ""
        active_acc: dict | None = None
        for acc in accounts:
            if isinstance(acc, dict) and acc.get("IsActive"):
                active_acc = acc
                break
        if active_acc is None:
            return ""
        active_username = self._as_text(active_acc.get("Username"), "")
        if not active_username:
            return ""
        # 优先: TestDemo 写入的 LastLoginTime (ISO 格式)
        last_login_time = self._as_text(active_acc.get("LastLoginTime"), "")
        if last_login_time:
            parsed = self._parse_iso_to_local(last_login_time)
            if parsed:
                return parsed
        # 回退: slave 持久化的 last_login_at
        login_state = self._load_account_login_state()
        state = login_state.get(active_username, {})
        return self._as_text(state.get("last_login_at"), "")

    @staticmethod
    def _parse_iso_to_local(iso_str: str) -> str:
        """将 ISO 8601 时间戳转为 '%Y-%m-%d %H:%M:%S' 本地时间。"""
        try:
            dt = datetime.fromisoformat(iso_str)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return ""

    def clear_runtime_status(self) -> None:
        try:
            self.runtime_status_path.unlink()
        except FileNotFoundError:
            return

    def _read_json(self, path: Path) -> object | None:
        try:
            return cast(object, json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as err:
            logger.warning("读取 JSON 文件失败: %s (%s)", path.name, err)
            return None

    def _load_account_login_state(self) -> dict[str, dict[str, object]]:
        data = self._read_json(self.account_login_state_path)
        if not isinstance(data, dict):
            return {}
        result: dict[str, dict[str, object]] = {}
        for username, raw_state in data.items():
            if not isinstance(username, str) or not username.strip():
                continue
            if not isinstance(raw_state, dict):
                continue
            result[username.strip()] = {
                "last_login_at": self._as_text(raw_state.get("last_login_at"), ""),
                "was_active": bool(raw_state.get("was_active")),
            }
        return result

    def _save_account_login_state(self, state: dict[str, dict[str, object]]) -> None:
        self.account_login_state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _touch_login_state(
        self, state: dict[str, dict[str, object]], username: str, is_active: bool,
    ) -> tuple[str, bool]:
        current = state.get(username, {})
        last_login_at = self._as_text(current.get("last_login_at"), "")
        was_active = bool(current.get("was_active"))
        if is_active and not was_active:
            last_login_at = self._now_text()
        changed = (
            last_login_at != self._as_text(current.get("last_login_at"), "")
            or was_active != is_active
        )
        if changed:
            state[username] = {
                "last_login_at": last_login_at,
                "was_active": is_active,
            }
        return last_login_at, changed

    @staticmethod
    def _now_text() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _as_text(value: object, default: str) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or default
        if value is None:
            return default
        return str(value).strip() or default

    @staticmethod
    def _as_int(value: object, default: int) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return default
