"""进程管理"""
from __future__ import annotations

import asyncio
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

    async def start_testdemo(self) -> bool:
        """启动 TestDemo.exe"""
        await self.kill_by_name("TestDemo")
        await asyncio.sleep(1)
        exe = self._base_dir / "TestDemo.exe"
        if not exe.exists():
            print(f"[错误] TestDemo.exe 不存在: {exe}")
            return False
        await asyncio.create_subprocess_exec(str(exe), cwd=str(self._base_dir))
        print("[启动] TestDemo.exe")
        return True

    async def stop_all(self) -> int:
        """停止所有目标进程"""
        total = 0
        for name in _KILL_TARGETS:
            total += await self.kill_by_name(name)
        print(f"[清理] 已停止 {total} 个进程")
        return total

    async def kill_by_name(self, name: str) -> int:
        """按名称前缀杀死进程"""
        killed = 0
        for proc in psutil.process_iter(["name"]):
            try:
                pname = proc.info.get("name", "")
                if pname and pname.lower().startswith(name.lower()):
                    proc.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return killed
