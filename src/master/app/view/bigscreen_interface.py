"""大屏模式页面 — 节点表格 + 账号池 + 操作按钮，一屏总览"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CheckBox,
    InfoBar,
    InfoBarPosition,
    PlainTextEdit,
    PushButton,
    ScrollArea,
    TableWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from common.protocol import TcpCommand
from master.app.common.style_sheet import StyleSheet
from master.app.core.account_pool import AccountPool
from master.app.core.node_manager import NodeManager
from master.app.core.tcp_commander import TcpCommander

_NODE_HEADERS = ["", "机器名", "IP地址", "挂机账号", "等级", "金币", "运行状态"]

# 操作按钮配置: (文本, FluentIcon, objectName)
_ACTION_BUTTONS = [
    ("提取合格出货", FIF.COMPLETED, "btnExport"),
    ("一键下发文件", FIF.SEND_FILL, "btnSendFile"),
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
        account_pool: AccountPool,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("bigscreenInterface")
        self._nm = node_mgr
        self._tcp = tcp_cmd
        self._pool = account_pool
        self._start_time = datetime.now()
        self._row_map: dict[str, int] = {}
        # P0: 行数据缓存，仅变化的列才 setText
        self._row_cache: dict[str, list[str]] = {}

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
        root.addWidget(self.table, stretch=6)

        # ═══ 底部区域：账号池 + 操作按钮 ═══
        bottom = QHBoxLayout()
        bottom.setSpacing(12)

        accountPanel = self._buildAccountPanel()
        bottom.addWidget(accountPanel, stretch=6)

        actionPanel = self._buildActionPanel()
        bottom.addWidget(actionPanel, stretch=4)

        bottomWidget = QWidget(self)
        bottomWidget.setLayout(bottom)
        bottomWidget.setFixedHeight(280)
        root.addWidget(bottomWidget)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        StyleSheet.BIGSCREEN_INTERFACE.apply(self)

        # 排序后重建行号映射，防止 _row_map 失效
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._rebuildRowMap)

        # ═══ 信号连接 ═══
        self._nm.node_online.connect(self._onNodeOnline)
        self._nm.node_updated.connect(self._onNodeUpdated)
        self._nm.node_offline.connect(self._onNodeUpdated)
        self._nm.stats_changed.connect(self._refreshHeader)
        self._pool.pool_changed.connect(self._refreshAccountStats)
        self._pool.pool_changed.connect(self._syncAccountEditFromPool)

        self._tcp.command_failed.connect(self._onCmdFailed)

        # 账号池文本编辑 → AccountPool 双向同步
        self._syncing = False  # 防循环
        self._debounceTimer = QTimer(self)
        self._debounceTimer.setSingleShot(True)
        self._debounceTimer.setInterval(300)
        self._debounceTimer.timeout.connect(self._syncAccountEditToPool)
        self.accountEdit.textChanged.connect(self._onAccountEditChanged)

        # 运行时长定时器
        self._uptimeTimer = QTimer(self)
        self._uptimeTimer.timeout.connect(self._refreshHeader)
        self._uptimeTimer.start(60_000)

        # 超时监控定时器
        self._watchdogTimer = QTimer(self)
        self._watchdogTimer.timeout.connect(self._checkStaleNodes)

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
        """节点实时状态表格"""
        table = TableWidget(self)
        table.setObjectName("nodeTable")
        table.setColumnCount(len(_NODE_HEADERS))
        table.setHorizontalHeaderLabels(_NODE_HEADERS)
        table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)

        # 隐藏行号
        table.verticalHeader().hide()

        header = table.horizontalHeader()
        # 状态列（圆点图标）固定窄宽
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 48)
        # 机器名、IP、挂机账号 stretch
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        # 等级、金币、运行状态 自适应
        for col in (4, 5, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        return table

    def _buildAccountPanel(self) -> QFrame:
        """底部左侧：账号池"""
        panel = QFrame(self)
        panel.setObjectName("accountPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        # 标题行带图标
        titleRow = QHBoxLayout()
        titleRow.setSpacing(6)
        icon_lbl = QLabel(panel)
        icon_lbl.setPixmap(FIF.PEOPLE.icon().pixmap(QSize(16, 16)))
        titleRow.addWidget(icon_lbl)
        lbl = QLabel("账号池", panel)
        lbl.setObjectName("panelTitle")
        titleRow.addWidget(lbl)
        titleRow.addStretch()
        layout.addLayout(titleRow)

        self.accountEdit = PlainTextEdit(panel)
        self.accountEdit.setObjectName("accountEdit")
        self.accountEdit.setPlaceholderText(
            "粘贴账号，每行一个\n格式: 账号----密码----邮箱----邮箱密码----[备注]"
        )
        self.accountEdit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.accountEdit, stretch=1)

        # 底部: 统计 + 超时监控
        footLayout = QHBoxLayout()
        footLayout.setSpacing(8)
        self._lblPoolStats = QLabel("总数:0  可用:0  使用中:0  已完成:0", panel)
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
        self._actionBtns[0].clicked.connect(self._exportQualified)
        self._actionBtns[1].clicked.connect(self._sendFileToAll)
        self._actionBtns[2].clicked.connect(self._deleteFileOnAll)
        self._actionBtns[3].clicked.connect(self._distributeKey)
        self._actionBtns[4].clicked.connect(self._startExeOnAll)
        self._actionBtns[5].clicked.connect(self._rebootAllPC)
        self._actionBtns[6].clicked.connect(self._stopExeOnAll)

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

    def _onNodeOnline(self, name: str) -> None:
        node = self._nm.nodes.get(name)
        if not node:
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._row_map[name] = row
        self._setRowData(row, node)

    def _onNodeUpdated(self, name: str) -> None:
        if name not in self._row_map:
            return
        node = self._nm.nodes.get(name)
        if node:
            self._setRowData(self._row_map[name], node)

    def _setRowData(self, row: int, node) -> None:
        name = node.machine_name
        # P0: 构建本次数据，与缓存比较，仅更新变化的列
        texts = [
            "",  # col 0 状态图标
            node.machine_name,
            node.ip,
            node.current_account or "--",
            str(node.level) if node.level else "--",
            node.jin_bi if node.jin_bi != "0" else "--",
            node.status,
        ]
        old_texts = self._row_cache.get(name, [])

        # 状态列：彩色圆点图标（仅状态变化时更新）
        if not old_texts or old_texts[6] != texts[6]:
            status_icon = self._status_icons.get(node.status, self._status_icon_default)
            status_item = self.table.item(row, 0)
            if status_item is None:
                status_item = QTableWidgetItem()
                status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 0, status_item)
            status_item.setIcon(status_icon)
            status_item.setText("")

        # 文本列：仅更新变化的列
        for col in range(1, len(texts)):
            text = texts[col]
            if old_texts and col < len(old_texts) and old_texts[col] == text:
                continue  # 值未变，跳过
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
            else:
                item.setText(text)

        # 运行状态列着色（仅状态变化时）
        if not old_texts or old_texts[6] != texts[6]:
            state_item = self.table.item(row, 6)
            if state_item:
                color = _STATUS_COLORS.get(node.status, _STATUS_COLOR_DEFAULT)
                state_item.setForeground(color)

        self._row_cache[name] = texts

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
            f"使用中:{p.in_use_count}  已完成:{p.completed_count}"
        )
        self._refreshHeader()

    def _syncAccountEditFromPool(self) -> None:
        """AccountPool 变化 → 更新文本框（从 AccountInterface 导入时触发）"""
        if self._syncing:
            return
        self._syncing = True
        lines = [acc.to_line() for acc in self._pool.accounts]
        self.accountEdit.setPlainText("\n".join(lines))
        self._syncing = False

    def _onAccountEditChanged(self) -> None:
        """文本框编辑 → 防抖同步到 AccountPool"""
        if self._syncing:
            return
        self._debounceTimer.start()

    def _syncAccountEditToPool(self) -> None:
        """防抖触发：实际同步到 AccountPool"""
        if self._syncing:
            return
        self._syncing = True
        self._pool.load_from_text(self.accountEdit.toPlainText())
        self._syncing = False

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
            text = self._pool.export_completed()
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            InfoBar.error(
                "导出失败", str(e),
                parent=self, position=InfoBarPosition.TOP, duration=5000,
            )
            return
        InfoBar.success(
            "导出成功", f"已导出 {self._pool.completed_count} 个账号",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _sendFileToAll(self) -> None:
        """一键下发文件"""
        path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if not path:
            return
        ips = self._getOnlineIPs()
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
        self._tcp.broadcast(ips, TcpCommand.UPDATE_TXT, content)
        self._nm.add_history("下发文件", f"{len(ips)} 个节点")
        InfoBar.success(
            "已下发", f"文件已发送到 {len(ips)} 个在线节点",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _deleteFileOnAll(self) -> None:
        """批量删除文件"""
        ips = self._getOnlineIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        self._tcp.broadcast(ips, TcpCommand.DELETE_FILE)
        self._nm.add_history("批量删除文件", f"{len(ips)} 个节点")
        InfoBar.success(
            "已发送", f"删除指令已发送到 {len(ips)} 个节点",
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
        ips = self._getOnlineIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        self._tcp.broadcast(ips, TcpCommand.UPDATE_KEY, key)
        self._nm.add_history("分发卡密", f"{len(ips)} 个节点")
        InfoBar.success(
            "卡密已分发", f"已发送到 {len(ips)} 个节点",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _startExeOnAll(self) -> None:
        """启动/重启脚本"""
        ips = self._getOnlineIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        self._tcp.broadcast(ips, TcpCommand.START_EXE)
        self._nm.add_history("启动脚本", f"{len(ips)} 个节点")
        InfoBar.success(
            "已发送", f"启动指令已发送到 {len(ips)} 个节点",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _rebootAllPC(self) -> None:
        """强制重启电脑"""
        ips = self._getOnlineIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        self._tcp.broadcast(ips, TcpCommand.REBOOT_PC)
        self._nm.add_history("重启电脑", f"{len(ips)} 个节点")
        InfoBar.success(
            "已发送", f"重启指令已发送到 {len(ips)} 个节点",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _stopExeOnAll(self) -> None:
        """停止脚本游戏"""
        ips = self._getOnlineIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        self._tcp.broadcast(ips, TcpCommand.STOP_EXE)
        self._nm.add_history("停止脚本", f"{len(ips)} 个节点")
        InfoBar.success(
            "已发送", f"停止指令已发送到 {len(ips)} 个节点",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
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
        """检查停滞节点并自动重启"""
        threshold_min = self.spinTimeout.value()
        now = datetime.now()
        rebooted = 0
        for node in self._nm.nodes.values():
            if node.status in ("离线", "断连"):
                continue
            elapsed = (now - node.last_status_update).total_seconds() / 60
            if elapsed >= threshold_min:
                self._tcp.send(node.ip, TcpCommand.REBOOT_PC)
                rebooted += 1
        if rebooted:
            self._nm.add_history("超时自动重启", f"{rebooted} 个节点")
            InfoBar.warning(
                "超时监控",
                f"已自动重启 {rebooted} 个停滞节点",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )

    def _onCmdFailed(self, ip: str, error: str) -> None:
        InfoBar.error(
            "通信失败", f"{ip}: {error}",
            parent=self, position=InfoBarPosition.TOP, duration=5000,
        )
