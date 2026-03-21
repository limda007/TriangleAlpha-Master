"""后台服务线程 — 在 QThread 内运行 asyncio event loop。"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import psutil
from PyQt6.QtCore import QThread, pyqtSignal

from common.protocol import HEARTBEAT_INTERVAL, GameState
from slave.auto_setup import check_rename, kill_remote_controls, setup_startup
from slave.command_handler import CommandHandler
from slave.heartbeat import HeartbeatService
from slave.log_reporter import LogReporter
from slave.logging_utils import configure_slave_logging, get_logger
from slave.state_store import RuntimeStatus, SlaveStateStore

logger = get_logger(__name__)


class SlaveBackend(QThread):
    """在独立线程中运行所有 asyncio 后台服务。"""

    heartbeat_sent = pyqtSignal(int, float, float)
    command_received = pyqtSignal(str)
    account_updated = pyqtSignal(int)
    script_status = pyqtSignal(bool)
    group_changed = pyqtSignal(str)
    log_entry = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, base_dir: Path, master_ip: str | None, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._base_dir = base_dir
        self._master_ip = master_ip
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = True
        self._tasks: list[asyncio.Task[None]] = []
        self._state_store = SlaveStateStore(base_dir)
        self._script_running = False
        self._script_started_at: float | None = None

    def run(self) -> None:
        """QThread 入口。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._run_services())
        except Exception as exc:
            logger.exception("后台服务线程异常")
            self.error_occurred.emit(str(exc))
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _run_services(self) -> None:
        start_time = time.time()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, setup_startup)
        await loop.run_in_executor(None, check_rename, self._base_dir)

        self._emit_account_count()

        settings = self._state_store.load_settings()
        self._heartbeat = HeartbeatService(
            master_ip=self._master_ip,
            on_sent=self._on_heartbeat,
            base_dir=self._base_dir,
        )
        self._heartbeat.set_group(settings.group)

        handler = CommandHandler(
            str(self._base_dir),
            on_command=self._on_command,
            on_account_updated=self._on_account_updated,
            on_group_changed=self._on_group_changed,
        )
        log_reporter = LogReporter(self._master_ip, self._heartbeat.machine_name)

        log_reporter.install()
        configure_slave_logging(gui_sink=self.log_entry.emit)

        handler.set_group_callback(self._heartbeat.set_group)
        logger.info("服务已启动")

        self._tasks = [
            asyncio.create_task(self._heartbeat.run()),
            asyncio.create_task(handler.run()),
            asyncio.create_task(log_reporter.run()),
            asyncio.create_task(kill_remote_controls(self._base_dir)),
            asyncio.create_task(self._status_writer(self._base_dir, self._heartbeat, start_time)),
            asyncio.create_task(self._process_monitor()),
            asyncio.create_task(self._status_reporter()),
        ]

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._heartbeat.stop()
            await handler.stop()
            await log_reporter.stop()
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _on_heartbeat(self, count: int, cpu: float, mem: float) -> None:
        self.heartbeat_sent.emit(count, cpu, mem)

    def _on_command(self, desc: str) -> None:
        self.command_received.emit(desc)

    def _on_account_updated(self, count: int) -> None:
        self.account_updated.emit(count)

    def _on_group_changed(self, group: str) -> None:
        try:
            self._state_store.save_group(group)
        except OSError:
            logger.exception("分组持久化失败: %s", group)
        self.group_changed.emit(group)
        logger.info("分组已持久化: %s", group)

    def _emit_account_count(self) -> None:
        acc_file = self._base_dir / "accounts.txt"
        if not acc_file.exists():
            return
        try:
            content = acc_file.read_text(encoding="utf-8")
        except OSError:
            return
        count = sum(1 for line in content.splitlines() if line.strip())
        self.account_updated.emit(count)

    async def _process_monitor(self) -> None:
        """每 10s 检测 TestDemo.exe 是否存活，状态变化时上报 master。"""
        was_running = False
        while self._running:
            running = self._is_testdemo_running()
            self._script_running = running
            self.script_status.emit(running)

            if running and not was_running:
                self._script_started_at = time.time()
                logger.info("检测到 TestDemo 启动")

            if was_running and not running:
                self._script_started_at = None
                try:
                    self._heartbeat.send_status(GameState.SCRIPT_STOPPED)
                    self._state_store.clear_runtime_status()
                    logger.info("检测到 TestDemo 停止，已上报脚本停止状态")
                except Exception:
                    logger.exception("发送脚本停止状态失败")

            was_running = running
            await asyncio.sleep(10)

    @staticmethod
    def _is_testdemo_running() -> bool:
        for proc in psutil.process_iter(["name"], ad_value=""):
            try:
                name = proc.info.get("name", "")
                if name and name.lower().startswith("testdemo"):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return False

    async def _status_reporter(self) -> None:
        """周期性发送 STATUS，保证 master 的状态链路持续闭环。"""
        while self._running:
            if self._script_running:
                snapshot = self._load_runtime_snapshot()
                try:
                    self._heartbeat.send_status(
                        snapshot.state,
                        snapshot.level,
                        snapshot.jin_bi,
                        snapshot.current_account,
                        snapshot.elapsed,
                    )
                except Exception:
                    logger.exception("周期性状态上报失败")
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    def _load_runtime_snapshot(self) -> RuntimeStatus:
        default_elapsed = "0"
        if self._script_started_at is not None:
            default_elapsed = str(max(0, int(time.time() - self._script_started_at)))
        snapshot = self._state_store.load_runtime_status(default_elapsed=default_elapsed)
        if snapshot.state == GameState.SCRIPT_STOPPED:
            return RuntimeStatus(
                state=GameState.RUNNING,
                level=snapshot.level,
                jin_bi=snapshot.jin_bi,
                current_account=snapshot.current_account,
                elapsed=snapshot.elapsed,
            )
        return snapshot

    async def _status_writer(self, base_dir: Path, heartbeat: HeartbeatService, start_time: float) -> None:
        """定期写入 slave_status.json。"""
        status_file = base_dir / "slave_status.json"
        while self._running:
            uptime_sec = int(time.time() - start_time)
            hours, remainder = divmod(uptime_sec, 3600)
            minutes, secs = divmod(remainder, 60)
            uptime_str = f"{hours}h{minutes:02d}m{secs:02d}s"

            status = {
                "pid": os.getpid(),
                "machine": heartbeat.machine_name,
                "status": "running",
                "group": heartbeat.group,
                "master_ip": self._master_ip or "",
                "script_running": self._script_running,
                "uptime": uptime_str,
                "uptime_sec": uptime_sec,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with contextlib.suppress(OSError):
                status_file.write_text(
                    json.dumps(status, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            await asyncio.sleep(30)

    def stop(self) -> None:
        """请求后台服务停止。"""
        self._running = False
        if self._loop and self._loop.is_running():
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._request_shutdown)

    def _request_shutdown(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
