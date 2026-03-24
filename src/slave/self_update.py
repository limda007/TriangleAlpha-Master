"""Slave 自更新辅助逻辑。"""
from __future__ import annotations

import base64
import binascii
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from common.protocol import SLAVE_SELF_UPDATE_FILENAME
from slave.logging_utils import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PreparedSelfUpdate:
    """已落盘的自更新阶段文件。"""

    filename: str
    target_path: Path
    pending_path: Path
    helper_path: Path


def is_self_update_filename(filename: str) -> bool:
    """判断给定文件是否是 slave 自更新包。"""
    return Path(filename).name.lower() == SLAVE_SELF_UPDATE_FILENAME.lower()


def prepare_self_update(
    base_dir: Path,
    payload: str,
    current_pid: int,
    current_executable: Path | None = None,
) -> PreparedSelfUpdate:
    """解码更新包，写入 pending 文件和替换 helper。"""
    filename, raw = _parse_self_update_payload(payload)
    target_path = _resolve_target_executable(base_dir, filename, current_executable)
    pending_path = target_path.with_suffix(f"{target_path.suffix}.pending")
    helper_path = target_path.with_suffix(f"{target_path.suffix}.update.cmd")
    backup_path = target_path.with_suffix(f"{target_path.suffix}.bak")

    pending_path.write_bytes(raw)
    helper_path.write_text(
        _build_update_helper_script(target_path, pending_path, backup_path, current_pid),
        encoding="utf-8",
    )

    logger.info("自更新文件已暂存: %s -> %s", filename, pending_path)
    return PreparedSelfUpdate(
        filename=filename,
        target_path=target_path,
        pending_path=pending_path,
        helper_path=helper_path,
    )


def launch_self_update_helper(update: PreparedSelfUpdate) -> None:
    """后台启动自更新 helper，等待当前进程退出后替换文件。"""
    if os.name != "nt":
        logger.warning("跳过自更新 helper：当前不是 Windows")
        return

    creation_flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    subprocess.Popen(  # noqa: S603
        ["cmd.exe", "/c", str(update.helper_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
    )
    logger.info("自更新 helper 已启动: %s", update.helper_path)


def _parse_self_update_payload(payload: str) -> tuple[str, bytes]:
    filename, sep, encoded = payload.partition("|")
    filename = Path(filename.strip()).name
    if not sep or not filename:
        raise ValueError("自更新格式错误，需要 filename|BASE64")

    encoded = encoded.strip()
    if encoded.startswith("BASE64:"):
        encoded = encoded[7:]
    if not encoded:
        raise ValueError("自更新内容为空")

    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error as err:
        raise ValueError(f"自更新包 Base64 解码失败: {err}") from err

    if not raw:
        raise ValueError("自更新包为空")
    return filename, raw


def _resolve_target_executable(base_dir: Path, filename: str, current_executable: Path | None) -> Path:
    if current_executable is not None:
        return current_executable.resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()

    target = (base_dir.resolve() / filename).resolve()
    if not target.is_relative_to(base_dir.resolve()):
        raise ValueError(f"自更新目标越界: {filename}")
    return target


def _build_update_helper_script(target: Path, pending: Path, backup: Path, current_pid: int) -> str:
    target_str = str(target)
    pending_str = str(pending)
    backup_str = str(backup)
    target_dir = str(target.parent)
    lines = [
        "@echo off",
        "setlocal enableextensions",
        f'set "TARGET={target_str}"',
        f'set "PENDING={pending_str}"',
        f'set "BACKUP={backup_str}"',
        f'set "TARGET_DIR={target_dir}"',
        f'set "WAIT_PID={current_pid}"',
        "",
        "for /l %%I in (1,1,120) do (",
        '    tasklist /FI "PID eq %WAIT_PID%" 2^>NUL | find /I "%WAIT_PID%" ^>NUL',
        "    if errorlevel 1 goto replace",
        "    timeout /t 1 /nobreak >NUL",
        ")",
        "goto cleanup",
        "",
        ":replace",
        "for /l %%I in (1,1,30) do (",
        '    if exist "%BACKUP%" del /f /q "%BACKUP%" >NUL 2^>^&1',
        '    if exist "%TARGET%" move /y "%TARGET%" "%BACKUP%" >NUL 2^>^&1',
        '    if not exist "%TARGET%" goto copy_new',
        "    timeout /t 1 /nobreak >NUL",
        ")",
        "goto cleanup",
        "",
        ":copy_new",
        'if not exist "%PENDING%" goto cleanup',
        'move /y "%PENDING%" "%TARGET%" >NUL 2^>^&1',
        'if not exist "%TARGET%" (',
        '    if exist "%BACKUP%" move /y "%BACKUP%" "%TARGET%" >NUL 2^>^&1',
        "    goto cleanup",
        ")",
        'start "" /d "%TARGET_DIR%" "%TARGET%" >NUL 2^>^&1',
        'if exist "%BACKUP%" del /f /q "%BACKUP%" >NUL 2^>^&1',
        "",
        ":cleanup",
        'start "" cmd /c del /f /q "%~f0" >NUL 2^>^&1',
        "exit /b 0",
    ]
    return "\r\n".join(lines) + "\r\n"
