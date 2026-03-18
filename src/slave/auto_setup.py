"""自动配置：开机自启、改名、远控查杀"""
from __future__ import annotations

import asyncio
import os
import platform
import sys
from pathlib import Path

import psutil


def setup_startup() -> None:
    """创建开机自启动快捷方式 (Windows only)"""
    if os.name != "nt":
        return
    try:
        import winreg  # type: ignore[import-not-found]  # Windows-only 模块

        exe_path = sys.executable if not getattr(sys, "frozen", False) else sys.argv[0]
        key = winreg.OpenKey(  # type: ignore[attr-defined]
            winreg.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,  # type: ignore[attr-defined]
        )
        winreg.SetValueEx(key, "TriangleAlphaSlave", 0, winreg.REG_SZ, exe_path)  # type: ignore[attr-defined]
        winreg.CloseKey(key)  # type: ignore[attr-defined]
        print("[自启动] 已注册")
    except Exception as e:
        print(f"[自启动] 失败: {e}")


def check_rename(base_dir: Path) -> None:
    """根据 '机器编号-{name}' 文件自动改名"""
    if os.name != "nt":
        return
    try:
        files = list(base_dir.glob("机器编号-*"))
        if not files:
            return
        target = files[0].stem.replace("机器编号-", "").strip()
        if not target:
            return
        if platform.node().lower() == target.lower():
            return
        print(f"[改名] 发现标识: {files[0].name} → {target}")
        # 需管理员权限
        os.system(f'wmic computersystem where name="%COMPUTERNAME%" rename {target}')  # noqa: S605
        print("[改名] 已提交，需重启生效")
    except Exception as e:
        print(f"[改名] 异常: {e}")


async def kill_remote_controls(base_dir: Path) -> None:
    """30 秒后根据 '关闭远控列表.txt' 杀死远控进程"""
    await asyncio.sleep(30)
    list_file = base_dir / "关闭远控列表.txt"
    if not list_file.exists():
        template = "# 一行一个进程名（不含 .exe）\n# ToDesk\n# SunloginClient\n# TeamViewer\n# AnyDesk\n"
        list_file.write_text(template, encoding="utf-8")
        print("[远控查杀] 已生成模板文件")
        return

    killed = 0
    for line in list_file.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or name.startswith("#") or name.startswith("//"):
            continue
        if name.lower().endswith(".exe"):
            name = name[:-4]
        for proc in psutil.process_iter(["name"]):
            try:
                pname = proc.info.get("name", "")
                if pname and pname.lower().startswith(name.lower()):
                    proc.kill()
                    killed += 1
                    print(f"[查杀] {pname}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    if killed:
        print(f"[远控查杀] 共清理 {killed} 个进程")
