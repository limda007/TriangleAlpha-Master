"""Slave 自更新辅助逻辑。"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PosixPath, WindowsPath

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
    guardian_pid: int | None = None
    guard_lock_path: Path | None = None


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
    guard_lock_path = _guard_lock_path()
    guardian_pid = _read_guardian_pid(guard_lock_path, current_pid)

    pending_path.write_bytes(raw)
    helper_path.write_text(
        _build_update_helper_script(
            target_path,
            pending_path,
            backup_path,
            current_pid,
            guardian_pid=guardian_pid,
            guard_lock_path=guard_lock_path if guard_lock_path.exists() else None,
        ),
        encoding="utf-8",
    )

    logger.info("自更新文件已暂存: %s -> %s", filename, pending_path)
    return PreparedSelfUpdate(
        filename=filename,
        target_path=target_path,
        pending_path=pending_path,
        helper_path=helper_path,
        guardian_pid=guardian_pid,
        guard_lock_path=guard_lock_path if guard_lock_path.exists() else None,
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
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for key in list(env):
        if key.startswith("_PYI_"):
            env.pop(key, None)
    subprocess.Popen(  # noqa: S603
        ["cmd.exe", "/c", str(update.helper_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
        env=env,
    )
    logger.info("自更新 helper 已启动: %s", update.helper_path)


def _parse_self_update_payload(payload: str) -> tuple[str, bytes]:
    filename, encoded, expected_size, expected_sha256 = _parse_self_update_metadata(payload)
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
    if expected_size is not None and len(raw) != expected_size:
        raise ValueError(f"自更新包大小不匹配: expected={expected_size} actual={len(raw)}")
    if expected_sha256 is not None:
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError("自更新包 SHA256 校验失败")
    return filename, raw


def _parse_self_update_metadata(payload: str) -> tuple[str, str, int | None, str | None]:
    parts = payload.split("|", 3)
    if len(parts) == 2:
        filename, encoded = parts
        safe_name = Path(filename.strip()).name
        if not safe_name:
            raise ValueError("自更新格式错误，需要 filename|BASE64")
        return safe_name, encoded, None, None
    if len(parts) != 4:
        raise ValueError("自更新格式错误，需要 filename|BASE64 或 filename|SHA256:...|SIZE:...|BASE64")

    filename, sha_part, size_part, encoded = parts
    safe_name = Path(filename.strip()).name
    if not safe_name:
        raise ValueError("自更新目标文件名为空")
    expected_sha256 = _parse_sha256_part(sha_part)
    expected_size = _parse_size_part(size_part)
    return safe_name, encoded, expected_size, expected_sha256


def _parse_sha256_part(sha_part: str) -> str:
    if not sha_part.startswith("SHA256:"):
        raise ValueError("自更新格式错误，缺少 SHA256")
    expected_sha256 = sha_part[7:].strip().lower()
    if len(expected_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha256):
        raise ValueError("自更新 SHA256 格式错误")
    return expected_sha256


def _parse_size_part(size_part: str) -> int:
    if not size_part.startswith("SIZE:"):
        raise ValueError("自更新格式错误，缺少 SIZE")
    raw_size = size_part[5:].strip()
    if not raw_size.isdigit():
        raise ValueError("自更新 SIZE 格式错误")
    return int(raw_size)


def _resolve_target_executable(base_dir: Path, filename: str, current_executable: Path | None) -> Path:
    if current_executable is not None:
        return current_executable.resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()

    target = (base_dir.resolve() / filename).resolve()
    if not target.is_relative_to(base_dir.resolve()):
        raise ValueError(f"自更新目标越界: {filename}")
    return target


def _guard_lock_path() -> Path:
    path_cls = WindowsPath if sys.platform.startswith("win") else PosixPath
    return path_cls(tempfile.gettempdir()) / "TriangleAlphaSlave.guard.pid"


def _read_guardian_pid(lock_path: Path, current_pid: int) -> int | None:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except (OSError, ValueError):
        return None
    if pid <= 0 or pid == current_pid:
        return None
    return pid


def _build_update_helper_script(
    target: Path,
    pending: Path,
    backup: Path,
    current_pid: int,
    *,
    guardian_pid: int | None = None,
    guard_lock_path: Path | None = None,
) -> str:
    target_str = str(target)
    pending_str = str(pending)
    backup_str = str(backup)
    target_dir = str(target.parent)
    guard_lock_str = str(guard_lock_path) if guard_lock_path is not None else ""
    lines = [
        "@echo off",
        "setlocal enableextensions",
        f'set "TARGET={target_str}"',
        f'set "PENDING={pending_str}"',
        f'set "BACKUP={backup_str}"',
        f'set "TARGET_DIR={target_dir}"',
        f'set "WAIT_PID={current_pid}"',
        f'set "GUARD_PID={guardian_pid or ""}"',
        f'set "GUARD_LOCK={guard_lock_str}"',
        'set "PYINSTALLER_RESET_ENVIRONMENT=1"',
        "",
        "for /l %%I in (1,1,120) do (",
        '    tasklist /FI "PID eq %WAIT_PID%" 2^>NUL | find /I "%WAIT_PID%" ^>NUL',
        "    if errorlevel 1 goto wait_guard_pid",
        "    timeout /t 1 /nobreak >NUL",
        ")",
        "goto cleanup",
        "",
        ":wait_guard_pid",
        'if not "%GUARD_PID%"=="" (',
        "    for /l %%I in (1,1,30) do (",
        '        tasklist /FI "PID eq %GUARD_PID%" 2^>NUL | find /I "%GUARD_PID%" ^>NUL',
        "        if errorlevel 1 goto wait_guard_lock",
        "        timeout /t 1 /nobreak >NUL",
        "    )",
        ")",
        "",
        ":wait_guard_lock",
        'if not "%GUARD_LOCK%"=="" (',
        "    for /l %%I in (1,1,15) do (",
        '        if not exist "%GUARD_LOCK%" goto replace',
        "        timeout /t 1 /nobreak >NUL",
        "    )",
        ")",
        "goto replace",
        "",
        ":replace",
        "timeout /t 2 /nobreak >NUL",
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
