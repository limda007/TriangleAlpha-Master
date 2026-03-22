"""Slave 本地配置与运行状态持久化。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from common.protocol import GameState
from slave.logging_utils import get_logger

logger = get_logger(__name__)
_DEFAULT_GROUP = "默认"


@dataclass(frozen=True)
class SlaveSettings:
    group: str = _DEFAULT_GROUP


@dataclass(frozen=True)
class RuntimeStatus:
    state: str = GameState.RUNNING
    level: int = 0
    jin_bi: str = "0"
    current_account: str = ""
    elapsed: str = "0"
    status_text: str = ""  # IPC 原始状态文本（如 "等待匹配"/"正在对局"）


class SlaveStateStore:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)

    @property
    def settings_path(self) -> Path:
        return self._base_dir / "slave_config.json"

    @property
    def runtime_status_path(self) -> Path:
        return self._base_dir / "runtime_status.json"

    def load_settings(self) -> SlaveSettings:
        data = self._read_json(self.settings_path)
        if not isinstance(data, dict):
            return SlaveSettings()
        return SlaveSettings(group=self._as_text(data.get("group"), _DEFAULT_GROUP))

    def save_settings(self, settings: SlaveSettings) -> None:
        self.settings_path.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2),
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
        result: list[dict[str, object]] = []
        for acc in data:
            if not isinstance(acc, dict):
                continue
            username = self._as_text(acc.get("Username"), "")
            if not username:
                continue
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
                "is_active": bool(acc.get("IsActive")),
            })
        return result

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
