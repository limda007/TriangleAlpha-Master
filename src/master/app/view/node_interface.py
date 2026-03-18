"""节点管理页面"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    MenuAnimationType,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    ScrollArea,
    SearchLineEdit,
    TableWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from common.protocol import TcpCommand
from master.app.components.stat_card import StatCard
from master.app.core.account_pool import AccountPool
from master.app.core.node_manager import NodeManager
from master.app.core.tcp_commander import TcpCommander

_HEADERS = ["状态", "机器名", "IP", "分组", "等级", "金币", "当前账号", "CPU%", "内存%", "最后心跳"]


class NodeInterface(ScrollArea):
    def __init__(self, node_mgr: NodeManager, tcp_cmd: TcpCommander,
                 account_pool: AccountPool, parent=None):
        super().__init__(parent)
        self.setObjectName("nodeInterface")
        self._nm = node_mgr
        self._tcp = tcp_cmd
        self._pool = account_pool

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.mainLayout = QVBoxLayout(self.view)
        self.mainLayout.setContentsMargins(24, 24, 24, 24)
        self.mainLayout.setSpacing(16)

        # -- 统计卡片 --
        statsLayout = QHBoxLayout()
        statsLayout.setSpacing(12)
        self.onlineCard = StatCard("在线节点", "0")
        self.totalCard = StatCard("总节点", "0")
        self.accountCard = StatCard("可用账号", "0")
        for card in (self.onlineCard, self.totalCard, self.accountCard):
            statsLayout.addWidget(card)
        self.mainLayout.addLayout(statsLayout)

        # -- 工具栏 --
        toolLayout = QHBoxLayout()
        self.searchBox = SearchLineEdit(self)
        self.searchBox.setPlaceholderText("搜索机器名/IP...")
        self.searchBox.setFixedWidth(250)
        toolLayout.addWidget(self.searchBox)

        self.groupCombo = ComboBox(self)
        self.groupCombo.addItem("全部")
        self.groupCombo.setFixedWidth(120)
        toolLayout.addWidget(self.groupCombo)
        toolLayout.addStretch()

        self.btnStart = PrimaryPushButton(FIF.PLAY, "启动选中", self)
        self.btnStop = PushButton(FIF.CLOSE, "停止选中", self)
        self.btnRebootPC = PushButton(FIF.POWER_BUTTON, "重启电脑", self)
        self.btnDistKeys = PushButton(FIF.SEND, "分发卡密", self)
        self.btnSendFile = PushButton(FIF.FOLDER, "发送文件", self)
        for btn in (self.btnStart, self.btnStop, self.btnRebootPC, self.btnDistKeys, self.btnSendFile):
            toolLayout.addWidget(btn)
        self.mainLayout.addLayout(toolLayout)

        # -- 表格 --
        self.table = TableWidget(self)
        self.table.setColumnCount(len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._showContextMenu)
        self.mainLayout.addWidget(self.table)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # -- 信号连接 --
        self._nm.node_online.connect(self._onNodeOnline)
        self._nm.node_updated.connect(self._onNodeUpdated)
        self._nm.node_offline.connect(self._onNodeUpdated)
        self._nm.stats_changed.connect(self._refreshStats)
        self._pool.pool_changed.connect(self._refreshStats)

        self.searchBox.textChanged.connect(self._applyFilter)
        self.groupCombo.currentTextChanged.connect(self._applyFilter)

        self.btnStart.clicked.connect(lambda: self._sendToSelected(TcpCommand.START_EXE))
        self.btnStop.clicked.connect(lambda: self._sendToSelected(TcpCommand.STOP_EXE))
        self.btnRebootPC.clicked.connect(lambda: self._sendToSelected(TcpCommand.REBOOT_PC))
        self.btnDistKeys.clicked.connect(self._distributeKeys)
        self.btnSendFile.clicked.connect(self._sendFile)

        self._tcp.command_failed.connect(self._onCmdFailed)

        self._row_map: dict[str, int] = {}

    # -- 节点事件 --

    def _onNodeOnline(self, name: str):
        node = self._nm.nodes[name]
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._row_map[name] = row
        self._setRowData(row, node)
        self._refreshGroups()
        InfoBar.success(
            "节点上线", f"{name} ({node.ip})",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )

    def _onNodeUpdated(self, name: str):
        if name not in self._row_map:
            return
        node = self._nm.nodes.get(name)
        if node:
            self._setRowData(self._row_map[name], node)

    def _setRowData(self, row: int, node):
        status_icon = {"在线": "\U0001f7e2", "离线": "\U0001f534", "断连": "\u26ab"}.get(node.status, "\U0001f7e1")
        items = [
            status_icon, node.machine_name, node.ip, node.group,
            str(node.level), node.jin_bi, node.current_account,
            f"{node.cpu_percent:.0f}%", f"{node.mem_percent:.0f}%",
            node.last_seen.strftime("%H:%M:%S"),
        ]
        for col, text in enumerate(items):
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem(text)
                self.table.setItem(row, col, item)
            else:
                item.setText(text)

    # -- 统计 --

    def _refreshStats(self):
        self.onlineCard.setValue(f"{self._nm.online_count}")
        self.totalCard.setValue(f"{self._nm.total_count}")
        self.accountCard.setValue(f"{self._pool.available_count}")

    def _refreshGroups(self):
        current = self.groupCombo.currentText()
        self.groupCombo.clear()
        self.groupCombo.addItem("全部")
        for g in self._nm.groups:
            self.groupCombo.addItem(g)
        idx = self.groupCombo.findText(current)
        if idx >= 0:
            self.groupCombo.setCurrentIndex(idx)

    # -- 搜索与过滤 --

    def _applyFilter(self):
        search = self.searchBox.text().lower()
        group = self.groupCombo.currentText()
        for name, row in self._row_map.items():
            node = self._nm.nodes.get(name)
            if not node:
                continue
            match_search = not search or search in name.lower() or search in node.ip
            match_group = group == "全部" or node.group == group
            self.table.setRowHidden(row, not (match_search and match_group))

    # -- 操作 --

    def _getSelectedIPs(self) -> list[tuple[str, str]]:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        result = []
        for name, row in self._row_map.items():
            if row in rows:
                node = self._nm.nodes.get(name)
                if node:
                    result.append((name, node.ip))
        return result

    def _sendToSelected(self, cmd: TcpCommand):
        selected = self._getSelectedIPs()
        if not selected:
            InfoBar.warning(
                "提示", "请先选择节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        for name, ip in selected:
            self._tcp.send(ip, cmd)
            self._nm.add_history(cmd.value, name)

    def _distributeKeys(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 key.txt", "", "Text (*.txt)")
        if not path:
            return
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        for node in self._nm.nodes.values():
            if node.status not in ("离线", "断连"):
                self._tcp.send(node.ip, TcpCommand.UPDATE_KEY, key)
        self._nm.add_history("分发卡密", "全部在线节点")
        InfoBar.success(
            "卡密已分发", f"已发送到 {self._nm.online_count} 个节点",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _sendFile(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if not path:
            return
        InfoBar.info(
            "提示", f"文件传输功能开发中: {path}",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    # -- 右键菜单 --

    def _showContextMenu(self, pos):
        menu = RoundMenu(parent=self.table)
        menu.addAction(Action(FIF.PLAY, "启动脚本", triggered=lambda: self._sendToSelected(TcpCommand.START_EXE)))
        menu.addAction(Action(FIF.CLOSE, "停止脚本", triggered=lambda: self._sendToSelected(TcpCommand.STOP_EXE)))
        menu.addSeparator()
        menu.addAction(Action(
            FIF.POWER_BUTTON, "重启电脑",
            triggered=lambda: self._sendToSelected(TcpCommand.REBOOT_PC),
        ))
        menu.exec(self.table.viewport().mapToGlobal(pos), aniType=MenuAnimationType.NONE)

    def _onCmdFailed(self, ip: str, error: str):
        InfoBar.error(
            "通信失败", f"{ip}: {error}",
            parent=self, position=InfoBarPosition.TOP, duration=5000,
        )
