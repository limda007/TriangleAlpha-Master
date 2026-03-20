"""大屏模式页面 — 节点表格 + 账号池 + 操作按钮，一屏总览"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    CheckBox,
    InfoBar,
    InfoBarPosition,
    MenuAnimationType,
    MessageBox,
    PlainTextEdit,
    PushButton,
    RoundMenu,
    ScrollArea,
    TableWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from common.protocol import TcpCommand
from master.app.common.style_sheet import StyleSheet
from master.app.core.account_db import AccountDB
from master.app.core.node_manager import NodeManager
from master.app.core.tcp_commander import TcpCommander

_NODE_HEADERS = ["", "机器名", "IP地址", "挂机账号", "等级", "金币", "运行时间", "运行状态"]

# 操作按钮配置: (文本, FluentIcon, objectName)
_ACTION_BUTTONS = [
    ("分发账号", FIF.SEND, "btnDistAccounts"),
    ("提取合格出货", FIF.COMPLETED, "btnExport"),
    ("下发账号文件", FIF.SEND_FILL, "btnSendFile"),
    ("批量删除文件", FIF.DELETE, "btnDeleteFile"),
    ("分发专属 Key", FIF.CERTIFICATE, "btnDistKey"),
    ("启动/重启脚本", FIF.PLAY_SOLID, "btnStartExe"),
    ("强制重启电脑", FIF.POWER_BUTTON, "btnRebootPC"),
    ("停止脚本游戏", FIF.CLOSE, "btnStopExe"),
]

# 状态色
_STATUS_COLORS: dict[str, QColor] = {
    "在线": QColor("#22c55e"),
    "离线": QColor("#ef4444"),
    "断连": QColor("#6b7280"),
}
_STATUS_COLOR_DEFAULT = QColor("#eab308")


def _colored_dot_icon(color: QColor, size: int = 16) -> QIcon:
    """生成纯色圆点图标"""
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, size - 4, size - 4)
    painter.end()
    return QIcon(px)


class BigScreenInterface(ScrollArea):
    """大屏模式：节点表格 + 账号池 + 操作按钮，复刻原版中控一屏布局"""

    def __init__(
        self,
        node_mgr: NodeManager,
        tcp_cmd: TcpCommander,
        account_pool: AccountDB,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("bigscreenInterface")
        self._nm = node_mgr
        self._tcp = tcp_cmd
        self._pool = account_pool
        self._start_time = datetime.now()
        self._row_map: dict[str, int] = {}

        # 预渲染状态图标缓存
        self._status_icons: dict[str, QIcon] = {
            status: _colored_dot_icon(color)
            for status, color in _STATUS_COLORS.items()
        }
        self._status_icon_default = _colored_dot_icon(_STATUS_COLOR_DEFAULT)

        self.view = QWidget(self)
        self.view.setObjectName("view")
        root = QVBoxLayout(self.view)
        root.setContentsMargins(16, 8, 16, 12)
        root.setSpacing(10)

        # ═══ 标题栏 ═══
        self._headerBar = self._buildHeader()
        root.addWidget(self._headerBar)

        # ═══ 节点实时表格 ═══
        self.table = self._buildNodeTable()

        # ═══ 底部区域：账号池 + 操作按钮 ═══
        bottom = QHBoxLayout()
        bottom.setSpacing(12)

        accountPanel = self._buildAccountPanel()
        bottom.addWidget(accountPanel, stretch=6)

        actionPanel = self._buildActionPanel()
        bottom.addWidget(actionPanel, stretch=4)

        bottomWidget = QWidget(self)
        bottomWidget.setLayout(bottom)

        # ═══ 可拖拽分割器：节点表格 ↔ 底部区域 ═══
        splitter = QSplitter(Qt.Orientation.Vertical, self.view)
        splitter.addWidget(self.table)
        splitter.addWidget(bottomWidget)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        root.addWidget(splitter)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        StyleSheet.BIGSCREEN_INTERFACE.apply(self)

        # 排序后重建行号映射，防止 _row_map 失效
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._rebuildRowMap)

        # 节点表格防抖刷新定时器
        self._tableRefreshTimer = QTimer(self)
        self._tableRefreshTimer.setSingleShot(True)
        self._tableRefreshTimer.setInterval(100)
        self._tableRefreshTimer.timeout.connect(self._refreshNodeTable)

        # ═══ 信号连接 ═══
        self._nm.node_online.connect(self._scheduleRefreshTable)
        self._nm.node_updated.connect(self._scheduleRefreshTable)
        self._nm.node_offline.connect(self._scheduleRefreshTable)
        self._nm.stats_changed.connect(self._refreshHeader)
        self._pool.pool_changed.connect(self._refreshAccountStats)
        self._pool.pool_changed.connect(self._refreshAccountTable)

        self._tcp.command_failed.connect(self._onCmdFailed)

        # 运行时长定时器
        self._uptimeTimer = QTimer(self)
        self._uptimeTimer.timeout.connect(self._refreshHeader)
        self._uptimeTimer.start(60_000)

        # 超时监控定时器
        self._watchdogTimer = QTimer(self)
        self._watchdogTimer.timeout.connect(self._checkStaleNodes)

        # 启动时从 DB 同步数据到 UI
        self._refreshAccountTable()
        self._refreshAccountStats()

    # ──────────────────────────────────────────────────────
    # 构建 UI
    # ──────────────────────────────────────────────────────

    def _buildHeader(self) -> QFrame:
        """标题栏: 图标 + 标题 + 在线/账号/运行时长徽章"""
        bar = QFrame(self)
        bar.setObjectName("headerBar")
        bar.setFixedHeight(44)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        # 标题图标
        icon_lbl = QLabel(bar)
        icon_lbl.setPixmap(FIF.COMMAND_PROMPT.icon().pixmap(QSize(20, 20)))
        layout.addWidget(icon_lbl)

        title = QLabel("TriangleAlpha 群控中心", bar)
        title.setObjectName("headerTitle")
        layout.addWidget(title)
        layout.addStretch()

        # 在线徽章
        self._badgeOnline = self._makeBadge(bar, FIF.WIFI, "在线: 0/0", "badgeOnline")
        layout.addWidget(self._badgeOnline)

        # 账号徽章
        self._badgeAccount = self._makeBadge(bar, FIF.PEOPLE, "账号: 0/0", "badgeAccount")
        layout.addWidget(self._badgeAccount)

        # 运行时长徽章
        self._badgeUptime = self._makeBadge(bar, FIF.STOP_WATCH, "0m", "badgeUptime")
        layout.addWidget(self._badgeUptime)

        return bar

    def _makeBadge(self, parent: QWidget, icon: FIF, text: str, obj_name: str) -> QFrame:
        """创建图标+文字的徽章组件"""
        badge = QFrame(parent)
        badge.setObjectName(obj_name)
        h = QHBoxLayout(badge)
        h.setContentsMargins(10, 4, 12, 4)
        h.setSpacing(6)

        icon_lbl = QLabel(badge)
        icon_lbl.setPixmap(icon.icon().pixmap(QSize(14, 14)))
        icon_lbl.setObjectName("badgeIcon")
        h.addWidget(icon_lbl)

        lbl = QLabel(text, badge)
        lbl.setObjectName("badgeText")
        h.addWidget(lbl)

        return badge

    def _setBadgeText(self, badge: QFrame, text: str) -> None:
        """更新徽章文字"""
        lbl = badge.findChild(QLabel, "badgeText")
        if lbl:
            lbl.setText(text)

    def _buildNodeTable(self) -> TableWidget:
        """节点实时状态表格（严格对齐 AccountInterface 写法）"""
        table = TableWidget(self)
        table.setObjectName("nodeTable")
        table.setColumnCount(len(_NODE_HEADERS))
        table.setHorizontalHeaderLabels(_NODE_HEADERS)
        table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.sortByColumn(1, Qt.SortOrder.AscendingOrder)  # 默认按机器名升序
        table.verticalHeader().hide()

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 48)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for col in (4, 5, 6, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(self._showNodeContextMenu)

        return table

    def _buildAccountPanel(self) -> QFrame:
        """底部左侧：账号池"""
        panel = QFrame(self)
        panel.setObjectName("accountPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        # 标题行带图标 + 上传按钮
        titleRow = QHBoxLayout()
        titleRow.setSpacing(6)
        icon_lbl = QLabel(panel)
        icon_lbl.setPixmap(FIF.PEOPLE.icon().pixmap(QSize(16, 16)))
        titleRow.addWidget(icon_lbl)
        lbl = QLabel("账号池", panel)
        lbl.setObjectName("panelTitle")
        titleRow.addWidget(lbl)
        titleRow.addStretch()
        btnUpload = PushButton(FIF.ADD, "上传账号", panel)
        btnUpload.setFixedHeight(28)
        btnUpload.clicked.connect(self._showImportDialog)
        titleRow.addWidget(btnUpload)
        layout.addLayout(titleRow)

        # 账号表格（替代文本框）
        _POOL_HEADERS = ["账号", "密码", "邮箱", "邮箱密码", "状态", "分配机器", "等级", "金币", "上传时间", "完成时间"]
        self.accountTable = TableWidget(panel)
        self.accountTable.setColumnCount(len(_POOL_HEADERS))
        self.accountTable.setHorizontalHeaderLabels(_POOL_HEADERS)
        self.accountTable.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.accountTable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.accountTable.setAlternatingRowColors(True)
        self.accountTable.verticalHeader().hide()
        header = self.accountTable.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(_POOL_HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.accountTable.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.accountTable, stretch=1)

        # 底部: 统计 + 超时监控
        footLayout = QHBoxLayout()
        footLayout.setSpacing(8)
        self._lblPoolStats = QLabel("总数:0  可用:0  运行中:0  已完成:0", panel)
        self._lblPoolStats.setObjectName("poolStats")
        footLayout.addWidget(self._lblPoolStats)
        footLayout.addStretch()

        self.chkWatchdog = CheckBox("超时监控", panel)
        self.chkWatchdog.setChecked(False)
        self.chkWatchdog.stateChanged.connect(self._onWatchdogToggled)
        footLayout.addWidget(self.chkWatchdog)

        lbl2 = QLabel("停滞超过", panel)
        footLayout.addWidget(lbl2)

        self.spinTimeout = QSpinBox(panel)
        self.spinTimeout.setRange(1, 120)
        self.spinTimeout.setValue(15)
        self.spinTimeout.setSuffix(" 分钟")
        self.spinTimeout.setFixedWidth(90)
        footLayout.addWidget(self.spinTimeout)

        layout.addLayout(footLayout)
        return panel

    def _buildActionPanel(self) -> QFrame:
        """底部右侧：7 个操作按钮（带图标）"""
        panel = QFrame(self)
        panel.setObjectName("actionPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        self._actionBtns: list[PushButton] = []
        for text, icon, obj_name in _ACTION_BUTTONS:
            btn = PushButton(icon, text, panel)
            btn.setObjectName(obj_name)
            btn.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            layout.addWidget(btn)
            self._actionBtns.append(btn)

        # 连接按钮信号
        self._actionBtns[0].clicked.connect(self._distributeAccounts)
        self._actionBtns[1].clicked.connect(self._exportQualified)
        self._actionBtns[2].clicked.connect(self._sendFileToAll)
        self._actionBtns[3].clicked.connect(self._deleteFileOnAll)
        self._actionBtns[4].clicked.connect(self._distributeKey)
        self._actionBtns[5].clicked.connect(self._startExeOnAll)
        self._actionBtns[6].clicked.connect(self._rebootAllPC)
        self._actionBtns[7].clicked.connect(self._stopExeOnAll)

        return panel

    # ──────────────────────────────────────────────────────
    # 节点表格更新
    # ──────────────────────────────────────────────────────

    def _rebuildRowMap(self) -> None:
        """排序后重建 machine_name → row 映射"""
        self._row_map.clear()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)  # col 1 = machine_name
            if item:
                self._row_map[item.text()] = row

    def _scheduleRefreshTable(self, _name: str = "") -> None:
        """防抖：100ms 内的多次信号合并为一次刷新"""
        if not self._tableRefreshTimer.isActive():
            self._tableRefreshTimer.start()

    def _refreshNodeTable(self) -> None:
        """全量重建节点表格（与 AccountInterface._refreshTable 同模式）"""
        nodes = list(self._nm.nodes.values())
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(nodes))
        for row, node in enumerate(nodes):
            self._setRowData(row, node)
        self.table.setSortingEnabled(True)
        self.table.setUpdatesEnabled(True)
        self._rebuildRowMap()

    def _setRowData(self, row: int, node) -> None:
        texts = [
            "",  # col 0 状态图标
            node.machine_name,
            node.ip,
            node.current_account or "--",
            str(node.level) if node.level else "--",
            node.jin_bi if node.jin_bi != "0" else "--",
            node.elapsed if node.elapsed != "0" else "--",
            node.game_state if node.game_state else node.status,
        ]

        # 状态列：彩色圆点图标
        status_icon = self._status_icons.get(node.status, self._status_icon_default)
        status_item = self.table.item(row, 0)
        if status_item is None:
            status_item = QTableWidgetItem()
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, status_item)
        status_item.setIcon(status_icon)
        status_item.setText("")

        # 文本列：全量写入
        for col in range(1, len(texts)):
            text = texts[col]
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
            else:
                item.setText(text)

        # 运行状态列着色
        state_item = self.table.item(row, 7)
        if state_item:
            color = _STATUS_COLORS.get(node.status, _STATUS_COLOR_DEFAULT)
            state_item.setForeground(color)

    # ──────────────────────────────────────────────────────
    # 标题栏刷新
    # ──────────────────────────────────────────────────────

    def _refreshHeader(self) -> None:
        online = self._nm.online_count
        total = self._nm.total_count
        self._setBadgeText(self._badgeOnline, f"在线: {online}/{total}")
        self._setBadgeText(
            self._badgeAccount,
            f"账号: {self._pool.available_count}/{self._pool.total_count}",
        )
        delta = datetime.now() - self._start_time
        total_min = int(delta.total_seconds() // 60)
        hours, mins = divmod(total_min, 60)
        self._setBadgeText(
            self._badgeUptime, f"{hours}h{mins:02d}m" if hours else f"{mins}m"
        )

    def _refreshAccountStats(self) -> None:
        p = self._pool
        self._lblPoolStats.setText(
            f"总数:{p.total_count}  可用:{p.available_count}  "
            f"运行中:{p.in_use_count}  已完成:{p.completed_count}"
        )
        self._refreshHeader()
        # 动态更新"提取合格出货"按钮文案
        btn = self._actionBtns[1]
        count = p.completed_count
        btn.setText(f"提取合格出货 ({count})" if count else "提取合格出货")

    def _refreshAccountTable(self) -> None:
        """从 AccountDB 刷新账号表格"""
        accounts = self._pool.get_all_accounts()
        self.accountTable.setRowCount(len(accounts))
        _MASK = "••••••••"
        for row, acc in enumerate(accounts):
            self.accountTable.setItem(row, 0, QTableWidgetItem(acc.username))
            self.accountTable.setItem(row, 1, QTableWidgetItem(_MASK))
            self.accountTable.setItem(row, 2, QTableWidgetItem(acc.bind_email))
            self.accountTable.setItem(row, 3, QTableWidgetItem(_MASK if acc.bind_email_password else ""))
            self.accountTable.setItem(row, 4, QTableWidgetItem(acc.status.value))
            self.accountTable.setItem(row, 5, QTableWidgetItem(acc.assigned_machine))
            self.accountTable.setItem(row, 6, QTableWidgetItem(str(acc.level) if acc.level else ""))
            self.accountTable.setItem(row, 7, QTableWidgetItem(acc.jin_bi if acc.jin_bi != "0" else ""))
            created_str = acc.created_at.strftime("%m-%d %H:%M") if acc.created_at else ""
            self.accountTable.setItem(row, 8, QTableWidgetItem(created_str))
            time_str = acc.completed_at.strftime("%m-%d %H:%M") if acc.completed_at else ""
            self.accountTable.setItem(row, 9, QTableWidgetItem(time_str))

    def _showImportDialog(self) -> None:
        """弹窗导入账号"""
        dlg = MessageBox("上传账号", "每行一个，格式: 账号----密码----邮箱----邮箱密码----[备注]", self.window())
        edit = PlainTextEdit(dlg)
        edit.setPlaceholderText("粘贴账号文本...")
        edit.setMinimumHeight(200)
        dlg.textLayout.addWidget(edit)
        dlg.yesButton.setText("导入")
        dlg.cancelButton.setText("取消")
        if dlg.exec():
            text = edit.toPlainText().strip()
            if text:
                self._pool.load_from_text(text)
                InfoBar.success(
                    "导入成功", "已导入账号",
                    parent=self, position=InfoBarPosition.TOP, duration=3000,
                )

    # ──────────────────────────────────────────────────────
    # 操作按钮
    # ──────────────────────────────────────────────────────

    def _getOnlineIPs(self) -> list[str]:
        """返回所有在线节点 IP"""
        return [
            n.ip
            for n in self._nm.nodes.values()
            if n.status not in ("离线", "断连")
        ]

    def _getSelectedOnlineNodes(self) -> tuple[list, bool]:
        """返回 (目标节点列表, 是否来自选中)"""
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            names = []
            for idx in selected_rows:
                item = self.table.item(idx.row(), 1)  # col 1 = machine_name
                if item:
                    names.append(item.text())
            nodes = [
                self._nm.nodes[n]
                for n in names
                if n in self._nm.nodes
                and self._nm.nodes[n].status not in ("离线", "断连")
            ]
            if nodes:
                return nodes, True
        # 回退：全部在线
        return [
            n for n in self._nm.nodes.values() if n.status not in ("离线", "断连")
        ], False

    def _getTargetIPs(self) -> tuple[list[str], bool]:
        """便捷包装，返回 (IP 列表, 是否选中模式)"""
        nodes, is_sel = self._getSelectedOnlineNodes()
        return [n.ip for n in nodes], is_sel

    def _confirmDangerous(self, title: str, content: str) -> bool:
        """危险操作二次确认对话框"""
        dlg = MessageBox(title, content, self.window())
        dlg.yesButton.setText("确认执行")
        dlg.cancelButton.setText("取消")
        return bool(dlg.exec())

    def _distributeAccounts(self) -> None:
        """分发账号：遍历在线节点，每台分配一个空闲账号并单播"""
        online_nodes, selected = self._getSelectedOnlineNodes()
        if not online_nodes:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        scope = "选中" if selected else "在线"
        distributed = 0
        for node in online_nodes:
            # 已有绑定账号的节点：重发
            existing = self._pool.get_account_for_machine(node.machine_name)
            if existing:
                self._tcp.send(node.ip, TcpCommand.UPDATE_TXT, existing.to_line())
                distributed += 1
                continue
            # 分配新账号
            acc = self._pool.allocate(node.machine_name)
            if acc is None:
                break
            self._tcp.send(node.ip, TcpCommand.UPDATE_TXT, acc.to_line())
            distributed += 1
        if distributed:
            self._nm.add_history("分发账号", f"{distributed} 个{scope}节点")
            InfoBar.success(
                "分发成功", f"已分发 {distributed} 个账号到{scope}节点",
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )
        else:
            InfoBar.info(
                "提示", "没有可分发的空闲账号",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )

    def _exportQualified(self) -> None:
        """提取合格出货"""
        if self._pool.completed_count == 0:
            InfoBar.warning(
                "提示", "没有已完成的账号",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出已完成账号", "finished_accounts.txt", "Text (*.txt)",
        )
        if not path:
            return
        try:
            text = self._pool.export_completed(mark_fetched=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            InfoBar.error(
                "导出失败", str(e),
                parent=self, position=InfoBarPosition.TOP, duration=5000,
            )
            return
        exported = len(text.splitlines()) - 1  # 减去表头
        InfoBar.success(
            "导出成功", f"已导出 {exported} 个账号",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _sendFileToAll(self) -> None:
        """下发账号文件 — 通过 UPDATE_TXT 覆盖 slave 端 accounts.txt"""
        path, _ = QFileDialog.getOpenFileName(self, "选择账号文件", "", "Text (*.txt)")
        if not path:
            return
        ips, selected = self._getTargetIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            InfoBar.error(
                "读取失败", str(e),
                parent=self, position=InfoBarPosition.TOP, duration=5000,
            )
            return
        scope = f"{len(ips)} 个{'选中' if selected else '在线'}节点"
        self._tcp.broadcast(ips, TcpCommand.UPDATE_TXT, content)
        self._nm.add_history("下发文件", scope)
        InfoBar.success(
            "已下发", f"文件已发送到 {scope}",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _deleteFileOnAll(self) -> None:
        """批量删除文件：弹窗输入文件名列表"""
        dlg = MessageBox("批量删除文件", "输入要删除的文件名（每行一个）", self.window())
        edit = PlainTextEdit(dlg)
        edit.setPlaceholderText("accounts.txt\nkey.txt\n...")
        edit.setMinimumHeight(120)
        dlg.textLayout.addWidget(edit)
        dlg.yesButton.setText("确认删除")
        dlg.cancelButton.setText("取消")
        if not dlg.exec():
            return
        filenames = [line.strip() for line in edit.toPlainText().splitlines() if line.strip()]
        if not filenames:
            InfoBar.warning(
                "提示", "未输入文件名",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        ips, selected = self._getTargetIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        scope = f"{len(ips)} 个{'选中' if selected else '在线'}节点"
        payload = "|".join(filenames)
        self._tcp.broadcast(ips, TcpCommand.DELETE_FILE, payload)
        self._nm.add_history("批量删除文件", scope, detail=", ".join(filenames))
        InfoBar.success(
            "已发送", f"删除指令已发送到 {scope}",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _distributeKey(self) -> None:
        """分发专属 Key"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 key.txt", "", "Text (*.txt)",
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                key = f.read().strip()
        except OSError as e:
            InfoBar.error(
                "读取失败", str(e),
                parent=self, position=InfoBarPosition.TOP, duration=5000,
            )
            return
        ips, selected = self._getTargetIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        scope = f"{'选中' if selected else '在线'}"
        self._tcp.broadcast(ips, TcpCommand.UPDATE_KEY, key)
        self._nm.add_history("分发卡密", f"{len(ips)} 个{scope}节点")
        InfoBar.success(
            "卡密已分发", f"已发送到 {len(ips)} 个{scope}节点",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _startExeOnAll(self) -> None:
        """启动/重启脚本"""
        ips, selected = self._getTargetIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        scope = f"{len(ips)} 个{'选中' if selected else '在线'}节点"
        self._tcp.broadcast(ips, TcpCommand.START_EXE)
        self._nm.add_history("启动脚本", scope)
        InfoBar.success(
            "已发送", f"启动指令已发送到 {scope}",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _rebootAllPC(self) -> None:
        """强制重启电脑"""
        ips, selected = self._getTargetIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        scope = f"{len(ips)} 个{'选中' if selected else '在线'}节点"
        if not self._confirmDangerous(
            "强制重启电脑",
            f"即将强制重启 {scope} 电脑，所有未保存数据将丢失",
        ):
            return
        self._tcp.broadcast(ips, TcpCommand.REBOOT_PC)
        self._nm.add_history("重启电脑", scope)
        InfoBar.success(
            "已发送", f"重启指令已发送到 {scope}",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _stopExeOnAll(self) -> None:
        """停止脚本游戏"""
        ips, selected = self._getTargetIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        scope = f"{len(ips)} 个{'选中' if selected else '在线'}节点"
        if not self._confirmDangerous(
            "停止脚本游戏",
            f"即将停止 {scope} 的脚本和游戏进程",
        ):
            return
        self._tcp.broadcast(ips, TcpCommand.STOP_EXE)
        self._nm.add_history("停止脚本", scope)
        InfoBar.success(
            "已发送", f"停止指令已发送到 {scope}",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    # ──────────────────────────────────────────────────────
    # 节点右键菜单
    # ──────────────────────────────────────────────────────

    def _showNodeContextMenu(self, pos) -> None:
        """节点表格右键菜单"""
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        name_item = self.table.item(row, 1)
        ip_item = self.table.item(row, 2)
        if not name_item or not ip_item:
            return
        machine_name = name_item.text()
        ip = ip_item.text()
        node = self._nm.nodes.get(machine_name)

        menu = RoundMenu(parent=self.table)
        # 复制操作
        menu.addAction(Action(FIF.COPY, "复制 IP", triggered=lambda: self._copyText(ip)))
        menu.addAction(
            Action(FIF.COPY, "复制机器名", triggered=lambda: self._copyText(machine_name))
        )
        menu.addSeparator()

        # 单节点命令（仅在线）
        if node and node.status not in ("离线", "断连"):
            menu.addAction(
                Action(
                    FIF.PLAY_SOLID,
                    "启动/重启脚本",
                    triggered=lambda: self._singleNodeCmd(
                        ip, TcpCommand.START_EXE, "启动脚本"
                    ),
                )
            )
            menu.addAction(
                Action(
                    FIF.CLOSE,
                    "停止脚本游戏",
                    triggered=lambda: self._singleNodeCmd(
                        ip, TcpCommand.STOP_EXE, "停止脚本"
                    ),
                )
            )
            menu.addSeparator()
            menu.addAction(
                Action(
                    FIF.POWER_BUTTON,
                    "重启电脑",
                    triggered=lambda: self._singleNodeReboot(ip, machine_name),
                )
            )
            menu.addSeparator()
            # 账号绑定操作
            bound = self._pool.get_account_for_machine(machine_name)
            if bound:
                menu.addAction(
                    Action(
                        FIF.REMOVE,
                        "释放绑定账号",
                        triggered=lambda: self._releaseNodeAccount(machine_name),
                    )
                )
            else:
                menu.addAction(
                    Action(
                        FIF.ADD,
                        "分配账号",
                        triggered=lambda: self._allocateNodeAccount(machine_name, ip),
                    )
                )

            menu.addSeparator()
            menu.addAction(
                Action(
                    FIF.TAG,
                    "设置分组",
                    triggered=lambda: self._setNodeGroup(ip, machine_name),
                )
            )

        menu.exec(self.table.viewport().mapToGlobal(pos), aniType=MenuAnimationType.NONE)

    def _copyText(self, text: str) -> None:
        """复制文本到剪贴板"""
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
        InfoBar.success(
            "已复制", text,
            parent=self, position=InfoBarPosition.TOP, duration=1500,
        )

    def _setNodeGroup(self, ip: str, machine_name: str) -> None:
        """设置节点分组"""
        dlg = MessageBox("设置分组", f"为 {machine_name} 设置分组名称", self.window())
        edit = PlainTextEdit(dlg)
        edit.setPlaceholderText("输入分组名...")
        edit.setMaximumHeight(40)
        dlg.textLayout.addWidget(edit)
        dlg.yesButton.setText("确认")
        if not dlg.exec():
            return
        group = edit.toPlainText().strip()
        if not group:
            return
        self._tcp.send(ip, TcpCommand.EXT_SET_GROUP, group)
        InfoBar.success(
            "已设置", f"{machine_name} → 分组 '{group}'",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )

    def _singleNodeCmd(self, ip: str, cmd: TcpCommand, label: str) -> None:
        """单节点发送命令"""
        self._tcp.send(ip, cmd)
        InfoBar.success(
            "已发送", f"{label}指令 → {ip}",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )

    def _singleNodeReboot(self, ip: str, name: str) -> None:
        """单节点重启（带确认）"""
        if not self._confirmDangerous(
            "重启电脑", f"即将强制重启 {name}，所有未保存数据将丢失"
        ):
            return
        self._tcp.send(ip, TcpCommand.REBOOT_PC)
        self._nm.add_history("重启电脑", name)
        InfoBar.success(
            "已发送", f"重启指令 → {name}",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )

    def _releaseNodeAccount(self, machine_name: str) -> None:
        """释放节点绑定的账号"""
        self._pool.release(machine_name)
        InfoBar.success(
            "已释放", f"{machine_name} 绑定账号已释放",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )

    def _allocateNodeAccount(self, machine_name: str, ip: str) -> None:
        """为单节点分配账号"""
        acc = self._pool.allocate(machine_name)
        if acc is None:
            InfoBar.warning(
                "提示", "没有可分配的空闲账号",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        self._tcp.send(ip, TcpCommand.UPDATE_TXT, acc.to_line())
        InfoBar.success(
            "已分配", f"{machine_name} ← {acc.username}",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )

    # ──────────────────────────────────────────────────────
    # 超时监控
    # ──────────────────────────────────────────────────────

    def _onWatchdogToggled(self, state: int) -> None:
        if state == Qt.CheckState.Checked.value:
            interval = self.spinTimeout.value() * 60_000
            self._watchdogTimer.start(interval)
        else:
            self._watchdogTimer.stop()

    def _checkStaleNodes(self) -> None:
        """检查停滞节点并自动软重启脚本"""
        threshold_min = self.spinTimeout.value()
        now = datetime.now()
        restarted = 0
        for node in self._nm.nodes.values():
            if node.status in ("离线", "断连"):
                continue
            if not node.game_state:  # 未启动脚本的节点跳过
                continue
            elapsed = (now - node.last_status_update).total_seconds() / 60
            if elapsed >= threshold_min:
                self._tcp.send(node.ip, TcpCommand.STOP_EXE)
                self._tcp.send(node.ip, TcpCommand.START_EXE)
                restarted += 1
        if restarted:
            self._nm.add_history("超时自动重启脚本", f"{restarted} 个节点")
            InfoBar.warning(
                "超时监控",
                f"已自动重启 {restarted} 个停滞节点的脚本",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )

    def _onCmdFailed(self, ip: str, error: str) -> None:
        InfoBar.error(
            "通信失败", f"{ip}: {error}",
            parent=self, position=InfoBarPosition.TOP, duration=5000,
        )
