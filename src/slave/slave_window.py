"""Slave 小窗口面板 + 系统托盘"""
from __future__ import annotations

import platform
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction, QCloseEvent, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

_MAX_LOG_LINES = 200


class SlaveWindow(QWidget):
    """被控端状态面板"""

    def __init__(self, base_dir: Path, master_ip: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base_dir = base_dir
        self._master_ip = master_ip
        self._machine_name = platform.node()
        self._start_time = time.time()

        self._init_ui()
        self._init_tray()

        # 运行时长定时器 — 每秒更新
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._update_uptime)
        self._uptime_timer.start(1000)

    # ── UI 构建 ──────────────────────────────────────────

    def _init_ui(self) -> None:
        self.setWindowTitle(f"TA-Slave | {self._machine_name}")
        self.setFixedSize(420, 380)

        # 强制背景填充，修复 PyInstaller onefile 渲染空白
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "SlaveWindow { background-color: #f5f5f5; }"
            "QLabel { color: #333; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)

        # 标题行
        title = QLabel(f"<b>TA-Slave</b> | {self._machine_name}")
        title.setStyleSheet("font-size: 14px; color: #111;")
        layout.addWidget(title)

        # 主控 + 分组
        row_info = QHBoxLayout()
        master_text = self._master_ip or "广播模式"
        row_info.addWidget(QLabel(f"主控: {master_text}"))
        self._lbl_group = QLabel("分组: 默认")
        row_info.addStretch()
        row_info.addWidget(self._lbl_group)
        layout.addLayout(row_info)

        # 状态 + 运行时长
        self._lbl_status = QLabel("状态: ● 启动中...")
        self._lbl_status.setStyleSheet("color: #e0a000;")
        layout.addWidget(self._lbl_status)

        # ── 业务区分隔线 ──
        sep1 = QLabel()
        sep1.setFrameShape(QLabel.Shape.HLine)  # type: ignore[arg-type]
        sep1.setStyleSheet("color: #555;")
        layout.addWidget(sep1)

        # 脚本状态
        self._lbl_script = QLabel("脚本: ● 未运行")
        self._lbl_script.setStyleSheet("color: #999;")
        layout.addWidget(self._lbl_script)

        # 账号数量
        self._lbl_accounts = QLabel("账号: 0 个")
        layout.addWidget(self._lbl_accounts)

        # CPU / MEM
        self._lbl_resources = QLabel("CPU: --% | MEM: --%")
        layout.addWidget(self._lbl_resources)

        # ── 日志区分隔线 ──
        sep2 = QLabel()
        sep2.setFrameShape(QLabel.Shape.HLine)  # type: ignore[arg-type]
        sep2.setStyleSheet("color: #555;")
        layout.addWidget(sep2)

        log_header = QLabel("日志")
        log_header.setStyleSheet("font-size: 12px; font-weight: bold;")
        layout.addWidget(log_header)

        # 日志区
        self._log_area = QPlainTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setMaximumBlockCount(_MAX_LOG_LINES)
        self._log_area.setStyleSheet(
            "font-family: 'Consolas', 'Courier New', monospace;"
            "font-size: 11px;"
            "background-color: #1e1e1e;"
            "color: #d4d4d4;"
        )
        layout.addWidget(self._log_area, stretch=1)

    # ── 系统托盘 ─────────────────────────────────────────

    def _init_tray(self) -> None:
        self._tray = QSystemTrayIcon(self)

        icon_path = self._base_dir / "icon.png"
        if icon_path.exists():
            icon = QIcon(str(icon_path))
        else:
            icon = QApplication.style().standardIcon(  # type: ignore[union-attr]
                QApplication.style().StandardPixmap.SP_ComputerIcon,  # type: ignore[union-attr]
            )
        self._tray.setIcon(icon)
        self.setWindowIcon(icon)

        menu = QMenu()
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.setToolTip(f"TA-Slave | {self._machine_name}")
        self._tray.show()

    # ── 信号槽 ──────────────────────────────────────────

    def on_heartbeat(self, _count: int, cpu: float, mem: float) -> None:
        """心跳信号 — 更新 CPU/MEM + 状态标绿"""
        self._lbl_status.setStyleSheet("color: #00c853;")
        self._lbl_resources.setText(f"CPU: {cpu:.0f}% | MEM: {mem:.0f}%")

    def on_command(self, desc: str) -> None:
        """指令信号 — 写日志 + 气泡"""
        ts = datetime.now().strftime("%H:%M")
        self.append_log(f"{ts} [指令] {desc}")
        if self._tray.isVisible():
            self._tray.showMessage("指令", desc, QSystemTrayIcon.MessageIcon.Information, 3000)

    def on_account_updated(self, count: int) -> None:
        """账号数量更新"""
        self._lbl_accounts.setText(f"账号: {count} 个已加载")

    def on_script_status(self, running: bool) -> None:
        """TestDemo 进程状态"""
        if running:
            self._lbl_script.setText("脚本: ● 运行中")
            self._lbl_script.setStyleSheet("color: #00c853;")
        else:
            self._lbl_script.setText("脚本: ● 未运行")
            self._lbl_script.setStyleSheet("color: #999;")

    def on_group_changed(self, group: str) -> None:
        """分组变更"""
        self._lbl_group.setText(f"分组: {group}")

    def append_log(self, text: str) -> None:
        self._log_area.appendPlainText(text)

    # ── 内部 ──────────────────────────────────────────

    def _update_uptime(self) -> None:
        """每秒刷新运行时长"""
        elapsed = int(time.time() - self._start_time)
        hours, remainder = divmod(elapsed, 3600)
        minutes, secs = divmod(remainder, 60)
        self._lbl_status.setText(f"状态: ● 运行中  {hours}h{minutes:02d}m{secs:02d}s")
        self._tray.setToolTip(
            f"TA-Slave | {self._machine_name} | {hours}h{minutes:02d}m{secs:02d}s",
        )

    # ── 事件处理 ─────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "TA-Slave", "已最小化到系统托盘",
            QSystemTrayIcon.MessageIcon.Information, 2000,
        )

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _quit_app(self) -> None:
        self._tray.hide()
        QApplication.instance().quit()  # type: ignore[union-attr]
