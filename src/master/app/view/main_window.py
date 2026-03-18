"""主窗口"""
from __future__ import annotations

import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, InfoBar, InfoBarPosition, NavigationItemPosition

from master.app.common.config import RESOURCE_DIR, cfg
from master.app.core.account_pool import AccountPool
from master.app.core.log_receiver import LogReceiverThread
from master.app.core.node_manager import NodeManager
from master.app.core.tcp_commander import TcpCommander
from master.app.core.udp_listener import UdpListenerThread
from master.app.view.account_interface import AccountInterface
from master.app.view.bigscreen_interface import BigScreenInterface
from master.app.view.history_interface import HistoryInterface
from master.app.view.log_interface import LogInterface
from master.app.view.setting_interface import SettingInterface


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()

        # 核心服务
        self.nodeManager = NodeManager(self)
        self.tcpCommander = TcpCommander(parent=self)
        self.accountPool = AccountPool(self)
        self.udpListener = UdpListenerThread(port=cfg.get(cfg.udpPort), parent=self)
        self.udpListener.message_received.connect(self.nodeManager.handle_udp_message)
        self.logReceiver = LogReceiverThread(port=cfg.get(cfg.tcpLogPort), parent=self)
        self.logReceiver.error_occurred.connect(self._onLogReceiverError)

        # 页面
        self.bigscreenInterface = BigScreenInterface(
            self.nodeManager, self.tcpCommander, self.accountPool, self,
        )
        self.accountInterface = AccountInterface(self.accountPool, self)
        self.historyInterface = HistoryInterface(self.nodeManager, self)
        self.logInterface = LogInterface(self)
        self.logInterface.set_receiver(self.logReceiver)
        self.settingInterface = SettingInterface(self)

        self._initWindow()
        self._initNavigation()

        # 启动
        self.udpListener.start()
        self.logReceiver.start()
        self._timeoutTimer = QTimer(self)
        self._timeoutTimer.timeout.connect(self.nodeManager.check_timeouts)
        self._timeoutTimer.start(5000)

    def _initNavigation(self):
        self.addSubInterface(self.bigscreenInterface, FIF.COMMAND_PROMPT, "大屏模式")
        self.navigationInterface.addSeparator()
        self.addSubInterface(self.accountInterface, FIF.PEOPLE, "账号管理", NavigationItemPosition.SCROLL)
        self.addSubInterface(self.historyInterface, FIF.HISTORY, "操作历史", NavigationItemPosition.SCROLL)
        self.addSubInterface(self.logInterface, FIF.DOCUMENT, "实时日志", NavigationItemPosition.SCROLL)
        self.addSubInterface(
            self.settingInterface, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM,
        )

    def _initWindow(self):
        self.resize(1200, 800)
        self.setMinimumWidth(900)
        self.setWindowTitle("TriangleAlpha 群控中心")

        # 应用图标
        icon_path = RESOURCE_DIR / "icon_256.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        if sys.platform != "darwin":
            self.navigationInterface.setAcrylicEnabled(True)

        if sys.platform == "darwin":
            self.setMicaEffectEnabled(False)
            self._fixMacOSTitleBar()

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.width() // 2 - self.width() // 2, geo.height() // 2 - self.height() // 2)

    def closeEvent(self, e):
        self.udpListener.stop()
        self.logReceiver.stop()
        super().closeEvent(e)

    def _fixMacOSTitleBar(self) -> None:
        self.setSystemTitleBarButtonVisible(True)
        self.titleBar.minBtn.hide()
        self.titleBar.maxBtn.hide()
        self.titleBar.closeBtn.hide()

    def _onLogReceiverError(self, msg: str) -> None:
        InfoBar.error(
            "日志服务异常", msg,
            parent=self, position=InfoBarPosition.TOP, duration=5000,
        )
