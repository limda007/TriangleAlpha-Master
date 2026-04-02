"""中控端入口"""
from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from master.app.common.config import RESOURCE_DIR
from master.app.view.main_window import MainWindow

# ── 日志配置 ──────────────────────────────────────────────
# 日志存放在 exe 所在目录（PyInstaller 打包后）或 cwd

_RUN_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
_CRASH_LOG = _RUN_DIR / "crash.log"
_APP_LOG = _RUN_DIR / "master.log"

_logger = logging.getLogger("trianglealpha.master")


def _setup_logging() -> None:
    """配置 master 运行日志，同时输出到控制台和文件。"""
    root = logging.getLogger("trianglealpha.master")
    root.setLevel(logging.INFO)
    root.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 滚动文件 (5 MB × 3)
    file_handler = RotatingFileHandler(
        str(_APP_LOG), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # __name__ 解析为 master.app.xxx，需要同步配置 "master" logger
    pkg_root = logging.getLogger("master")
    pkg_root.setLevel(logging.INFO)
    pkg_root.propagate = False
    pkg_root.addHandler(console)
    pkg_root.addHandler(file_handler)

    # common.protocol 等共享模块的 logger
    common_root = logging.getLogger("common")
    common_root.setLevel(logging.INFO)
    common_root.propagate = False
    common_root.addHandler(console)
    common_root.addHandler(file_handler)


def _write_crash_log(exc_type, exc_value, exc_tb) -> None:
    """将未捕获异常追加写入 crash.log，同时保留默认 stderr 输出。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    entry = f"\n{'='*60}\n[{timestamp}] Uncaught Exception\n{tb_text}"

    try:
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError:
        pass  # 文件系统不可用时静默失败

    _logger.critical("未捕获异常，已写入 %s", _CRASH_LOG, exc_info=(exc_type, exc_value, exc_tb))

    # 调用原始 hook 保留 stderr 输出
    sys.__excepthook__(exc_type, exc_value, exc_tb)


# ── 入口 ──────────────────────────────────────────────────


def main():
    _setup_logging()
    sys.excepthook = _write_crash_log
    _logger.info("Master 启动")

    app = QApplication(sys.argv)
    icon_path = RESOURCE_DIR / "icon_256.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    exit_code = app.exec()
    _logger.info("Master 正常退出 (code=%d)", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
