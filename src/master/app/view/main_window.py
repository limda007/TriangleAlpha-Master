"""主窗口"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, InfoBar, InfoBarPosition, NavigationItemPosition

from common.protocol import TcpCommand
from master.app.common.config import RESOURCE_DIR, cfg
from master.app.core.account_db import AccountDB
from master.app.core.log_receiver import LogReceiverThread
from master.app.core.node_manager import NodeManager
from master.app.core.tcp_commander import TcpCommander
from master.app.core.udp_listener import UdpListenerThread
from master.app.view.account_interface import AccountInterface
from master.app.view.bigscreen_interface import BigScreenInterface
from master.app.view.history_interface import HistoryInterface
from master.app.view.log_interface import LogInterface
from master.app.view.setting_interface import SettingInterface


def _get_db_path() -> Path:
    """accounts.db 放在 master exe 同目录"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "accounts.db"
    return Path(__file__).resolve().parents[4] / "accounts.db"


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()

        # 核心服务
        self.nodeManager = NodeManager(self)
        self.tcpCommander = TcpCommander(parent=self)
        self.accountPool = AccountDB(_get_db_path(), parent=self)
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

        # 节点重连时自动重发绑定账号
        self.nodeManager.node_online.connect(self._onNodeReconnect)

        # slave STATUS 上报 → 同步等级/金币/状态到 AccountDB
        self.nodeManager.node_status_reported.connect(self._syncAccountFromNode)

        # slave ACCOUNT_SYNC → 批量 upsert 账号到 AccountDB
        self.nodeManager.account_sync_received.connect(self._onAccountSync)

        # TestDemo NEED_ACCOUNT → 自动分配空闲账号
        self.nodeManager.need_account.connect(self._onNeedAccount)

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
        self.resize(1600, 800)
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
        self.accountPool.close()
        self.udpListener.stop()
        self.logReceiver.stop()
        self.tcpCommander.stop()
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

    def _onNodeReconnect(self, machine_name: str) -> None:
        """节点上线时自动重发其绑定的账号"""
        acc = self.accountPool.get_account_for_machine(machine_name)
        if acc is None:
            return
        node = self.nodeManager.nodes.get(machine_name)
        if node:
            self.tcpCommander.send(node.ip, TcpCommand.UPDATE_TXT, acc.to_line())

    def _syncAccountFromNode(self, machine_name: str) -> None:
        """slave STATUS 上报 → 同步等级/金币/状态到 AccountDB"""
        node = self.nodeManager.nodes.get(machine_name)
        if not node:
            return
        self.accountPool.update_from_status(
            machine_name, node.level, node.jin_bi, node.game_state,
            current_account=node.current_account,
        )

    def _onAccountSync(self, machine_name: str, accounts: object) -> None:
        """slave ACCOUNT_SYNC → 同步 level/jin_bi + 封禁检测"""
        if not isinstance(accounts, list):
            return
        self.accountPool.upsert_from_sync(machine_name, accounts)

    def _onNeedAccount(self, machine_name: str) -> None:
        """TestDemo NEED_ACCOUNT → 自动分配账号并下发。

        优先重发该机器已绑定的未完成账号（accounts.json 被删等情况），
        无绑定账号时从池中分配新的空闲账号。
        """
        node = self.nodeManager.nodes.get(machine_name)
        if not node:
            return
        existing = self.accountPool.get_account_for_machine(machine_name)
        if existing:
            # 该机器有未完成账号 → 重新下发，让 TestDemo 继续跑
            self.tcpCommander.send(node.ip, TcpCommand.UPDATE_TXT, existing.to_line())
            return
        acc = self.accountPool.allocate(machine_name)
        if acc is None:
            return
        self.tcpCommander.send(node.ip, TcpCommand.UPDATE_TXT, acc.to_line())
