"""自动配置：开机自启、改名、远控查杀"""
from __future__ import annotations

import asyncio
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import psutil

from slave.logging_utils import get_logger

if os.name == "nt":
    import winreg  # type: ignore[import-not-found]

logger = get_logger(__name__)


_TASK_NAME = "TriangleAlphaSlave"


def setup_startup() -> None:
    """注册多重自启：计划任务 + 启动项快捷方式 + 注册表兜底。"""
    if os.name != "nt":
        return
    try:
        exe_path = _resolve_executable_path()
        start_command = _build_start_command(exe_path)

        # 清除同名旧注册表自启（重新写入）
        _remove_legacy_registry_startup()

        task_ok = _create_startup_task(start_command)
        shortcut_ok = _create_startup_shortcut(exe_path)
        registry_ok = _set_registry_startup(start_command)

        if not any((task_ok, shortcut_ok, registry_ok)):
            logger.error("所有自启动注册方式均失败: %s", exe_path)
            return

        logger.info(
            "自启动注册完成: exe=%s task=%s shortcut=%s registry=%s",
            exe_path,
            task_ok,
            shortcut_ok,
            registry_ok,
        )
    except (OSError, subprocess.SubprocessError):
        logger.exception("自启动注册失败")


def _resolve_executable_path() -> Path:
    raw = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    return Path(raw).resolve()


def _build_start_command(exe_path: Path) -> str:
    return f'cmd.exe /c start "" /d "{exe_path.parent}" "{exe_path}"'


def _default_startup_dir() -> Path:
    return (
        Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def _run_cscript(script_text: str) -> subprocess.CompletedProcess[str]:
    script_path = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "_ta_startup_helper.vbs"
    script_path.write_text(script_text, encoding="utf-8")
    try:
        return subprocess.run(  # noqa: S603
            ["cscript", "//Nologo", str(script_path)],
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        script_path.unlink(missing_ok=True)


def _resolve_startup_dir() -> Path:
    """优先通过 WScript.SpecialFolders 获取系统真实启动目录。"""
    fallback = _default_startup_dir()
    try:
        result = _run_cscript('WScript.Echo CreateObject("WScript.Shell").SpecialFolders("Startup")\n')
    except (OSError, subprocess.SubprocessError):
        logger.exception("查询真实启动目录失败，回退默认路径")
        return fallback
    startup_dir = result.stdout.strip()
    if result.returncode == 0 and startup_dir:
        return Path(startup_dir)
    stderr = result.stderr.strip() or result.stdout.strip()
    if stderr:
        logger.warning("读取启动目录失败，回退默认路径: %s", stderr)
    return fallback


def _create_startup_task(start_command: str) -> bool:
    """创建计划任务，显式指定工作目录，兼容嵌套路径。"""
    result = subprocess.run(  # noqa: S603
        [
            "schtasks", "/Create",
            "/TN", _TASK_NAME,
            "/TR", start_command,
            "/SC", "ONSTART",
            "/RL", "HIGHEST",
            "/F",
        ],
        shell=False,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("计划任务创建失败: %s", result.stderr.strip() or result.stdout.strip())
        return False
    logger.info("计划任务已注册: %s", start_command)
    return True


def _create_startup_shortcut(exe_path: Path) -> bool:
    """在当前用户启动文件夹创建快捷方式（每次运行覆盖写入）。"""
    try:
        startup_dir = _resolve_startup_dir()
        startup_dir.mkdir(parents=True, exist_ok=True)
        shortcut_path = startup_dir / f"{_TASK_NAME}.lnk"

        # 使用 VBScript 创建快捷方式，避免依赖 pywin32
        result = _run_cscript(
            f'Set ws = CreateObject("WScript.Shell")\n'
            f'Set sc = ws.CreateShortcut("{shortcut_path}")\n'
            f'sc.TargetPath = "{exe_path}"\n'
            f'sc.WorkingDirectory = "{Path(exe_path).parent}"\n'
            f'sc.WindowStyle = 7\n'
            f'sc.Save\n'
        )
        if result.returncode != 0:
            logger.warning("启动文件夹快捷方式创建失败: %s", result.stderr.strip() or result.stdout.strip())
            return False
        logger.info("启动文件夹快捷方式已创建: %s", shortcut_path)
        return True
    except (OSError, subprocess.SubprocessError):
        logger.exception("创建启动文件夹快捷方式失败")
        return False


def _set_registry_startup(start_command: str) -> bool:
    """写入 HKCU Run，兼容部分机器自定义启动目录。"""
    try:
        key = winreg.CreateKey(  # type: ignore[attr-defined]
            winreg.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
            r"Software\Microsoft\Windows\CurrentVersion\Run",
        )
        try:
            winreg.SetValueEx(  # type: ignore[attr-defined]
                key,
                _TASK_NAME,
                0,
                winreg.REG_SZ,  # type: ignore[attr-defined]
                start_command,
            )
        finally:
            winreg.CloseKey(key)  # type: ignore[attr-defined]
        logger.info("注册表自启动已写入: %s", start_command)
        return True
    except (OSError, subprocess.SubprocessError):
        logger.exception("写入注册表自启动失败")
        return False


def _remove_startup_shortcut() -> None:
    """删除启动文件夹中的快捷方式。"""
    try:
        startup_dir = _resolve_startup_dir()
        shortcut_path = startup_dir / f"{_TASK_NAME}.lnk"
        if shortcut_path.exists():
            shortcut_path.unlink()
            logger.info("已删除启动文件夹快捷方式: %s", shortcut_path)
    except (OSError, subprocess.SubprocessError):
        logger.exception("删除启动文件夹快捷方式失败")


def _remove_legacy_registry_startup() -> None:
    """清除旧版注册表自启条目。"""
    try:
        key = winreg.OpenKey(  # type: ignore[attr-defined]
            winreg.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,  # type: ignore[attr-defined]
        )
        try:
            winreg.DeleteValue(key, _TASK_NAME)  # type: ignore[attr-defined]
            logger.info("已清除旧版注册表自启条目")
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)  # type: ignore[attr-defined]
    except OSError:
        pass  # 清理失败不影响主流程


def uninstall() -> None:
    """卸载自清理：删除计划任务 + 注册表残留。"""
    if os.name != "nt":
        return
    # 删除计划任务
    subprocess.run(  # noqa: S603
        ["schtasks", "/Delete", "/TN", _TASK_NAME, "/F"],
        shell=False, check=False, capture_output=True,
    )
    # 清除注册表残留
    _remove_legacy_registry_startup()
    # 删除启动文件夹快捷方式
    _remove_startup_shortcut()
    logger.info("卸载清理完成")


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
    except (OSError, subprocess.SubprocessError):
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
