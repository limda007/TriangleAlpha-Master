"""进程监控 — 检测 TestDemo/Launcher 存活状态，管理重启与浏览器清理。"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import psutil

from slave.logging_utils import get_logger
from slave.process_manager import ProcessManager

logger = get_logger(__name__)


class IpcSnapshotProvider(Protocol):
    """IPC 快照接口，避免对 LocalIpcReceiver 的直接依赖。"""

    def snapshot(self) -> tuple[object, float]: ...


class ProcessWatcher:
    """周期检测 TestDemo/Launcher 是否存活，根据策略触发重启。"""

    _BROWSER_NAMES = {"msedge", "chrome", "firefox", "iexplore", "browser"}
    _GAME_PATH_KEYWORDS = ("steamapps", "delta force", "tencent", "wegame", "qbblink")
    _RECOVERY_SEC = 60
    _MAX_RESTART = 3
    _IPC_STALE_SEC = 60
    _IPC_RESTART_COOLDOWN = 120

    def __init__(
        self,
        base_dir: Path,
        ipc: IpcSnapshotProvider,
        *,
        on_script_status: Callable[[bool], None],
        on_started: Callable[[], None],
        on_stopped: Callable[[], None],
    ) -> None:
        self._base_dir = base_dir
        self._ipc = ipc
        self._on_script_status = on_script_status
        self._on_started = on_started
        self._on_stopped = on_stopped
        self._running = True
        self._script_started_at: float | None = None

    @property
    def script_started_at(self) -> float | None:
        return self._script_started_at

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """主循环：每 10s 检测进程状态。"""
        was_running = False
        pm = ProcessManager(str(self._base_dir))
        testdemo_down_since: float | None = None
        testdemo_ever_alive = False
        ipc_restart_cooldown: float = 0
        restart_count = 0

        while self._running:
            testdemo_alive = is_process_alive("testdemo")
            launcher_alive = is_process_alive("trianglealpha.launcher")
            running = testdemo_alive or launcher_alive
            self._on_script_status(running)

            if testdemo_alive:
                testdemo_ever_alive = True
                restart_count = 0

            if running and not was_running:
                self._script_started_at = time.time()
                testdemo_down_since = None
                self._on_started()
                logger.info("检测到 TestDemo 启动")

            if was_running and not running:
                self._script_started_at = None
                testdemo_down_since = None
                testdemo_ever_alive = False
                restart_count = 0
                self._on_stopped()

            # TestDemo 挂了但 Launcher 还在 → 等恢复期后重启 Launcher
            if not testdemo_alive and launcher_alive and testdemo_ever_alive:
                if testdemo_down_since is None:
                    testdemo_down_since = time.time()
                elif time.time() - testdemo_down_since >= self._RECOVERY_SEC:
                    if restart_count >= self._MAX_RESTART:
                        if restart_count == self._MAX_RESTART:
                            logger.error(
                                "TestDemo 反复崩溃，已重启 %d 次仍无法恢复，停止重试",
                                restart_count,
                            )
                            restart_count += 1
                    else:
                        restart_count += 1
                        logger.warning(
                            "TestDemo 已挂超过 %ds，重启 Launcher (%d/%d)",
                            self._RECOVERY_SEC, restart_count, self._MAX_RESTART,
                        )
                        testdemo_down_since = None
                        testdemo_ever_alive = False
                        try:
                            await pm.kill_by_name("TriangleAlpha.Launcher")
                            await asyncio.sleep(2)
                            await pm.start_launcher()
                        except Exception:
                            logger.exception("重启 Launcher 失败")
            else:
                testdemo_down_since = None

            # TestDemo 存活但 IPC 静默超过阈值 → 可能卡死，重启 Launcher
            ipc_data, ipc_age = self._ipc.snapshot()
            now = time.time()
            ipc_ever_received = ipc_data is not None
            script_running_secs = (
                now - self._script_started_at if self._script_started_at else 0
            )
            ipc_silent = ipc_ever_received and ipc_age >= self._IPC_STALE_SEC
            ipc_never_started = not ipc_ever_received and script_running_secs >= self._IPC_STALE_SEC
            if (
                testdemo_alive
                and (ipc_silent or ipc_never_started)
                and now - ipc_restart_cooldown >= self._IPC_RESTART_COOLDOWN
            ):
                if ipc_never_started:
                    logger.warning("TestDemo 运行 %.0fs 从未发送 IPC，疑似卡死，重启 Launcher", script_running_secs)
                else:
                    logger.warning("TestDemo IPC 静默 %.0fs，疑似卡死，重启 Launcher", ipc_age)
                ipc_restart_cooldown = now
                try:
                    await pm.kill_by_name("TriangleAlpha.Launcher")
                    await asyncio.sleep(2)
                    await pm.start_launcher()
                except Exception:
                    logger.exception("重启 Launcher 失败 (IPC 超时)")

            was_running = running
            if ipc_data is not None:
                kill_browsers()
            await asyncio.sleep(10)


def is_process_alive(name: str) -> bool:
    """检测指定进程是否存活（不区分大小写，忽略 .exe 后缀）"""
    target = name.lower()
    for proc in psutil.process_iter(["name"], ad_value=""):
        try:
            pname = proc.info.get("name", "")
            if pname and pname.lower().removesuffix(".exe") == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def is_testdemo_running() -> bool:
    """检测脚本进程是否存活（Launcher 或 TestDemo 任一即可）"""
    targets = ("testdemo", "trianglealpha.launcher")
    for proc in psutil.process_iter(["name"], ad_value=""):
        try:
            name = proc.info.get("name", "")
            if name and name.lower().removesuffix(".exe") in targets:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def kill_browsers() -> None:
    """关闭浏览器进程（游戏安全中心等弹窗页面）"""
    killed = 0
    for proc in psutil.process_iter(["name", "exe"], ad_value=""):
        try:
            pname = proc.info.get("name", "")
            if not pname:
                continue
            base = pname.lower().removesuffix(".exe")
            if base not in ProcessWatcher._BROWSER_NAMES:
                continue
            exe_path = (proc.info.get("exe") or "").lower()
            if any(kw in exe_path for kw in ProcessWatcher._GAME_PATH_KEYWORDS):
                continue
            proc.kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if killed:
        logger.info("已关闭 %d 个浏览器进程", killed)
