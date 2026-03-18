"""后台服务线程 — 在 QThread 内运行 asyncio event loop"""
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

from slave.auto_setup import check_rename, kill_remote_controls, setup_startup
from slave.command_handler import CommandHandler
from slave.heartbeat import HeartbeatService
from slave.log_reporter import LogReporter


class SlaveBackend(QThread):
    """在独立线程中运行所有 asyncio 后台服务

    注意: 所有 pyqtSignal.emit() 从本线程（QThread）调用。
    Qt6 自动识别跨线程连接（QueuedConnection），信号参数（int/float/str/bool）
    均为 Qt 可序列化类型，因此是线程安全的。
    回调中不得直接操作 GUI 对象。
    """

    # (心跳计数, CPU%, MEM%)
    heartbeat_sent = pyqtSignal(int, float, float)
    # 指令描述
    command_received = pyqtSignal(str)
    # 账号数量更新
    account_updated = pyqtSignal(int)
    # TestDemo 进程状态 (running?)
    script_status = pyqtSignal(bool)
    # 分组变更
    group_changed = pyqtSignal(str)
    # 日志行（供 GUI 显示）
    log_entry = pyqtSignal(str)
    # 错误信息
    error_occurred = pyqtSignal(str)

    def __init__(self, base_dir: Path, master_ip: str | None, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._base_dir = base_dir
        self._master_ip = master_ip
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = True
        # C1: 存储任务引用，用于优雅关闭时取消
        self._tasks: list[asyncio.Task[None]] = []

    def run(self) -> None:
        """QThread 入口 — 创建 asyncio loop 并运行所有服务"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._run_services())
        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            # 清理所有未完成的 task
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _run_services(self) -> None:
        start_time = time.time()

        # P1: 同步操作放入线程池，不阻塞 event loop
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, setup_startup)
        await loop.run_in_executor(None, check_rename, self._base_dir)

        # 启动时读取已有账号数
        self._emit_account_count()

        # 构建核心服务，注入回调
        heartbeat = HeartbeatService(
            master_ip=self._master_ip,
            on_sent=self._on_heartbeat,
        )
        handler = CommandHandler(
            str(self._base_dir),
            on_command=self._on_command,
            on_account_updated=self._on_account_updated,
            on_group_changed=self._on_group_changed,
        )
        log_reporter = LogReporter(self._master_ip, heartbeat.machine_name)

        # stdout 拦截
        log_reporter.install()

        # 分组回调（心跳用）
        handler.set_group_callback(heartbeat.set_group)

        self.log_entry.emit("[就绪] 服务已启动")

        self._tasks = [
            asyncio.create_task(heartbeat.run()),
            asyncio.create_task(handler.run()),
            asyncio.create_task(log_reporter.run()),
            asyncio.create_task(kill_remote_controls(self._base_dir)),
            asyncio.create_task(self._status_writer(self._base_dir, heartbeat, start_time)),
            asyncio.create_task(self._process_monitor()),
        ]

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass
        finally:
            heartbeat.stop()
            await handler.stop()
            await log_reporter.stop()
            # C1: 取消所有未完成的子任务
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # ── 回调 ──────────────────────────────────────────

    def _on_heartbeat(self, count: int, cpu: float, mem: float) -> None:
        self.heartbeat_sent.emit(count, cpu, mem)

    def _on_command(self, desc: str) -> None:
        self.command_received.emit(desc)

    def _on_account_updated(self, count: int) -> None:
        self.account_updated.emit(count)

    def _on_group_changed(self, group: str) -> None:
        self.group_changed.emit(group)

    def _emit_account_count(self) -> None:
        """读取 accounts.txt 行数并发射信号"""
        acc_file = self._base_dir / "accounts.txt"
        if acc_file.exists():
            try:
                content = acc_file.read_text(encoding="utf-8")
                count = sum(1 for line in content.splitlines() if line.strip())
                self.account_updated.emit(count)
            except OSError:
                pass

    # ── 进程监控 ──────────────────────────────────────

    async def _process_monitor(self) -> None:
        """每 10s 检测 TestDemo.exe 是否存活（P0: 从 5s 提升到 10s）"""
        while self._running:
            running = self._is_testdemo_running()
            self.script_status.emit(running)
            await asyncio.sleep(10)

    @staticmethod
    def _is_testdemo_running() -> bool:
        # P0: 使用 attrs 缓存机制减少系统调用开销
        for proc in psutil.process_iter(["name"], ad_value=""):
            try:
                name = proc.info.get("name", "")
                if name and name.lower().startswith("testdemo"):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return False

    # ── 状态文件 ──────────────────────────────────────

    async def _status_writer(
        self, base_dir: Path, heartbeat: HeartbeatService, start_time: float,
    ) -> None:
        """定期写入 slave_status.json"""
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
                "uptime": uptime_str,
                "uptime_sec": uptime_sec,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with contextlib.suppress(OSError):
                status_file.write_text(
                    json.dumps(status, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            # P1: 写入间隔从 5s → 30s，减少频繁 I/O
            await asyncio.sleep(30)

    # C1: 重写 stop()，通过取消任务实现优雅关闭
    def stop(self) -> None:
        """请求后台服务停止"""
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._request_shutdown)

    def _request_shutdown(self) -> None:
        """在 event loop 线程中取消所有任务（由 call_soon_threadsafe 调用）"""
        for task in self._tasks:
            if not task.done():
                task.cancel()
