"""自动配置：开机自启、改名、远控查杀"""
from __future__ import annotations

import asyncio
import os
import platform
import subprocess
import sys
from pathlib import Path

import psutil

from slave.logging_utils import get_logger

logger = get_logger(__name__)


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
        logger.info("自启动已注册")
    except Exception:
        logger.exception("自启动注册失败")


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
        # M10: 白名单校验，仅允许字母数字、连字符、下划线
        import re
        if not re.fullmatch(r"[A-Za-z0-9\-_]+", target):
            logger.warning("改名拒绝非法字符: %s", target)
            return
        if platform.node().lower() == target.lower():
            return
        logger.info("发现改名标识: %s -> %s", files[0].name, target)
        subprocess.run(  # noqa: S603
            ["wmic", "computersystem", "where", f'name="{platform.node()}"', "rename", target],
            shell=False,
            check=False,
        )
        logger.info("改名已提交，需重启生效")
    except Exception:
        logger.exception("改名流程异常")


async def kill_remote_controls(base_dir: Path) -> None:
    """30 秒后根据 '关闭远控列表.txt' 杀死远控进程"""
    await asyncio.sleep(30)
    list_file = base_dir / "关闭远控列表.txt"
    if not list_file.exists():
        template = (
            "# ========================================\n"
            "# 关闭远控列表\n"
            "# 一行一个进程名（不含 .exe），# 开头为注释\n"
            "# 去掉 # 即启用对应项\n"
            "# ========================================\n"
            "\n"
            "# ── 远程桌面 ──\n"
            "ToDesk\n"
            "ToDesk_Service\n"
            "SunloginClient\n"
            "SunloginRemote\n"
            "TeamViewer\n"
            "TeamViewer_Service\n"
            "AnyDesk\n"
            "# RustDesk\n"
            "# parsec\n"
            "# RemoteDesktop\n"
            "\n"
            "# ── 远程协助 ──\n"
            "# TightVNC\n"
            "# tvnserver\n"
            "# UltraVNC\n"
            "# winvnc\n"
            "# RealVNC\n"
            "# vncserver\n"
            "\n"
            "# ── 其他远控 ──\n"
            "# LookMyPC\n"
            "# GotoHTTP\n"
            "# Radmin\n"
            "# rserver3\n"
            "# Ammyy\n"
            "# AA_v3\n"
        )
        list_file.write_text(template, encoding="utf-8")
        logger.info("已生成远控查杀模板文件")
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
                if pname and pname.lower() == name.lower() + ".exe":
                    proc.kill()
                    killed += 1
                    logger.info("查杀: %s", pname)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    if killed:
        logger.info("远控查杀共清理 %s 个进程", killed)
