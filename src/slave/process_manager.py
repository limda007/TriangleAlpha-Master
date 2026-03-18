"""进程管理"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import psutil

_KILL_TARGETS = [
    "TestDemo",
    "steam",
    "steamwebhelper",
    "steamerrorreporter",
    "DeltaForce",
    "Client-Win64-Shipping",
    "df_launcher",
    "SteamService",
    "DeltaForceLauncher",
    "DeltaForceClient",
    "DeltaForceClient-Win64-Shipping",
]


class ProcessManager:
    def __init__(self, base_dir: str):
        self._base_dir = Path(base_dir)
        self._process: asyncio.subprocess.Process | None = None

    async def start_testdemo(self) -> bool:
        """启动 TestDemo.exe"""
        await self.kill_by_name("TestDemo")
        await asyncio.sleep(1)
        exe = self._base_dir / "TestDemo.exe"
        if not exe.exists():
            print(f"[错误] TestDemo.exe 不存在: {exe}")
            return False
        self._process = await asyncio.create_subprocess_exec(str(exe), cwd=str(self._base_dir))
        print("[启动] TestDemo.exe")
        return True

    async def stop_all(self) -> int:
        """停止所有目标进程"""
        # 先停止管理的子进程
        if self._process is not None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (ProcessLookupError, TimeoutError):
                # M9: 包裹 kill()，进程可能已退出
                with contextlib.suppress(ProcessLookupError):
                    self._process.kill()
            self._process = None

        # H6: 单次遍历检查所有目标，避免重复遍历 + 使用精确匹配
        targets_lower = {name.lower() for name in _KILL_TARGETS}
        total = 0
        for proc in psutil.process_iter(["name"]):
            try:
                pname = proc.info.get("name", "")
                if not pname:
                    continue
                # 精确匹配：去掉 .exe 后缀后比较
                base_name = pname.lower()
                if base_name.endswith(".exe"):
                    base_name = base_name[:-4]
                if base_name in targets_lower:
                    proc.kill()
                    total += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        print(f"[清理] 已停止 {total} 个进程")
        return total

    async def kill_by_name(self, name: str) -> int:
        """按名称精确匹配杀死进程（不区分大小写，自动处理 .exe 后缀）"""
        killed = 0
        target = name.lower()
        for proc in psutil.process_iter(["name"]):
            try:
                pname = proc.info.get("name", "")
                if not pname:
                    continue
                base_name = pname.lower()
                if base_name.endswith(".exe"):
                    base_name = base_name[:-4]
                if base_name == target:
                    proc.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return killed
