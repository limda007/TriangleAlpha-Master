"""大屏模式页面 — 节点表格 + 账号池 + 操作按钮，一屏总览"""
from __future__ import annotations

from datetime import datetime

import httpx
from PyQt6.QtCore import QObject, QSize, Qt, QThread, QTimer, pyqtSignal
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
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    CaptionLabel,
    CheckBox,
    ComboBox,
    GroupHeaderCardWidget,
    HyperlinkLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MenuAnimationType,
    MessageBox,
    Pivot,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    ScrollArea,
    SpinBox,
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

_BALANCE_API = "http://gpu1.xinyuocr.xyz:8889/api/qrcode/balance"


class _BalanceWorker(QThread):
    """后台查询验证码余额"""

    # (total, money, free, error)
    result_ready = pyqtSignal(float, float, float, str)

    def __init__(self, key_code: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key_code

    def run(self) -> None:
        try:
            resp = httpx.get(_BALANCE_API, params={"keyCode": self._key}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success") or data.get("Success"):
                info = data.get("data") or data.get("Data") or {}
                total = float(info.get("totalBalance") or info.get("TotalBalance") or 0)
                money = float(info.get("money") or info.get("Money") or 0)
                free = float(info.get("freeMoney") or info.get("FreeMoney") or 0)
                self.result_ready.emit(total, money, free, "")
            else:
                err = data.get("error") or data.get("Error") or "未知错误"
                self.result_ready.emit(0, 0, 0, err)
        except Exception as exc:
            self.result_ready.emit(0, 0, 0, f"网络异常: {exc}")


_NODE_HEADERS = [
    "", "机器名", "IP地址", "挂机账号", "等级", "金币",
    "运行时间", "运行状态", "CPU%", "内存%", "版本",
    "补齐队友", "武器配置", "下号等级", "舔包次数",
]

# 状态色
_STATUS_COLORS: dict[str, QColor] = {
    "在线": QColor("#22c55e"),
    "离线": QColor("#ef4444"),
    "断连": QColor("#6b7280"),
}
_STATUS_COLOR_DEFAULT = QColor("#eab308")

# 账号状态色
_ACCOUNT_STATUS_COLORS: dict[str, QColor] = {
    "空闲中": QColor("#6b7280"),
    "运行中": QColor("#3b82f6"),
    "已完成": QColor("#22c55e"),
    "已取号": QColor("#8b5cf6"),
}

_WEAPONS = [
    "G17", "G17_不带药", "QSZ92G", "左轮357",
    "AK74", "CAR15", "M16A4突击步枪",
    "UZI冲锋枪", "勇士冲锋枪", "MP5冲锋枪", "野牛冲锋枪",
    "M870霰弹枪", "M1014霰弹枪",
    "Mini14射手步枪", "VSS射手步枪",
]
_DEFAULT_TEAMMATE_TEXT = "关闭"
_DEFAULT_WEAPON = "G17_不带药"
_DEFAULT_LEVEL = 18
_DEFAULT_LOOT = 8


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
        self._pending_updates: set[str] = set()

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

        # 节点分组筛选
        nodeContainer = QWidget(self.view)
        nodeLayout = QVBoxLayout(nodeContainer)
        nodeLayout.setContentsMargins(0, 0, 0, 0)
        nodeLayout.setSpacing(4)
        filterRow = QHBoxLayout()
        filterRow.addStretch()
        filterLbl = QLabel("分组筛选:", nodeContainer)
        filterRow.addWidget(filterLbl)
        self._groupCombo = ComboBox(nodeContainer)
        self._groupCombo.addItem("全部")
        self._groupCombo.setFixedWidth(120)
        self._groupCombo.currentTextChanged.connect(self._onGroupFilterChanged)
        filterRow.addWidget(self._groupCombo)
        nodeLayout.addLayout(filterRow)
        nodeLayout.addWidget(self.table)

        # ═══ 底部区域：账号池 + 操作按钮 ═══
        bottom = QHBoxLayout()
        bottom.setSpacing(12)

        accountPanel = self._buildAccountPanel()
        bottom.addWidget(accountPanel, stretch=7)

        actionPanel = self._buildActionPanel()
        bottom.addWidget(actionPanel, stretch=3)

        bottomWidget = QWidget(self)
        bottomWidget.setLayout(bottom)

        # ═══ 可拖拽分割器：节点表格 ↔ 底部区域 ═══
        splitter = QSplitter(Qt.Orientation.Vertical, self.view)
        splitter.addWidget(nodeContainer)
        splitter.addWidget(bottomWidget)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
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
        self._nm.node_offline.connect(self._onNodeOffline)
        self._nm.stats_changed.connect(self._refreshHeader)
        self._pool.pool_changed.connect(self._refreshAccountStats)
        self._pool.pool_changed.connect(self._refreshAccountTable)

        self._tcp.command_failed.connect(self._onCmdFailed)

        # 节点选中 → 回填配置面板
        self.table.selectionModel().selectionChanged.connect(
            self._onNodeSelectionChanged
        )

        # 离线通知限频
        self._offline_batch: list[str] = []
        self._offlineBatchTimer = QTimer(self)
        self._offlineBatchTimer.setSingleShot(True)
        self._offlineBatchTimer.setInterval(5000)
        self._offlineBatchTimer.timeout.connect(self._flushOfflineBatch)

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
        for col in range(4, len(_NODE_HEADERS)):
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
        header.setMinimumSectionSize(60)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        self.accountTable.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
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
        """底部右侧：Pivot 分页面板（操作/文件/配置）"""
        panel = QFrame(self)
        panel.setObjectName("actionPanel")
        outerLayout = QVBoxLayout(panel)
        outerLayout.setContentsMargins(10, 8, 10, 8)
        outerLayout.setSpacing(6)

        # Pivot 导航 + QStackedWidget
        self._actionPivot = Pivot(panel)
        self._actionStack = QStackedWidget(panel)
        outerLayout.addWidget(self._actionPivot, 0, Qt.AlignmentFlag.AlignLeft)
        outerLayout.addWidget(self._actionStack)

        # ── Page 1: 操作 ──
        opPage = QWidget()
        opPage.setObjectName("opPage")
        opLayout = QVBoxLayout(opPage)
        opLayout.setContentsMargins(0, 6, 0, 0)
        opLayout.setSpacing(5)

        _OP_BUTTONS = [
            ("一键分发文件", FIF.PLAY, "btnOneClick"),
            ("启动/重启脚本", FIF.PLAY_SOLID, "btnStartExe"),
            ("停止脚本游戏", FIF.CLOSE, "btnStopExe"),
            ("强制重启电脑", FIF.POWER_BUTTON, "btnRebootPC"),
        ]
        self._actionBtns: list[PushButton] = []
        for text, icon, obj_name in _OP_BUTTONS:
            btn = PushButton(icon, text, opPage)
            btn.setObjectName(obj_name)
            btn.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
            )
            opLayout.addWidget(btn)
            self._actionBtns.append(btn)

        # ── Page 2: 文件 ──
        filePage = QWidget()
        filePage.setObjectName("filePage")
        fileLayout = QVBoxLayout(filePage)
        fileLayout.setContentsMargins(0, 6, 0, 0)
        fileLayout.setSpacing(5)

        _FILE_BUTTONS = [
            ("提取账号", FIF.COMPLETED, "btnExtract"),
            ("导出所有", FIF.SAVE, "btnExportAll"),
            ("下发账号文件", FIF.SEND_FILL, "btnSendFile"),
            ("批量删除文件", FIF.DELETE, "btnDeleteFile"),
            ("清理单机账号", FIF.REMOVE, "btnCleanAccounts"),
            ("分发专属 Key", FIF.CERTIFICATE, "btnDistKey"),
        ]
        for text, icon, obj_name in _FILE_BUTTONS:
            btn = PushButton(icon, text, filePage)
            btn.setObjectName(obj_name)
            btn.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
            )
            fileLayout.addWidget(btn)
            self._actionBtns.append(btn)

        # ── Page 3: 配置 ──
        cfgPage = self._buildConfigPage()

        # ── Page 4: 验证码 ──
        tokenPage = self._buildTokenPage()

        # 注册到 Pivot
        self._addActionPage(opPage, "opPage", "操作")
        self._addActionPage(filePage, "filePage", "文件")
        self._addActionPage(cfgPage, "cfgPage", "配置")
        self._addActionPage(tokenPage, "tokenPage", "验证码")

        self._actionStack.currentChanged.connect(self._onActionPageChanged)
        self._actionStack.setCurrentWidget(opPage)
        self._actionPivot.setCurrentItem("opPage")

        # 连接按钮信号（操作: 0-3, 文件: 4-9）
        self._actionBtns[0].clicked.connect(self._oneClickStart)
        self._actionBtns[1].clicked.connect(self._startExeOnAll)
        self._actionBtns[2].clicked.connect(self._stopExeOnAll)
        self._actionBtns[3].clicked.connect(self._rebootAllPC)
        self._actionBtns[4].clicked.connect(self._extractCompleted)
        self._actionBtns[5].clicked.connect(self._exportAll)
        self._actionBtns[6].clicked.connect(self._sendFileToAll)
        self._actionBtns[7].clicked.connect(self._deleteFileOnAll)
        self._actionBtns[8].clicked.connect(self._cleanStandaloneAccounts)
        self._actionBtns[9].clicked.connect(self._distributeKey)

        return panel

    def _addActionPage(self, widget: QWidget, key: str, text: str) -> None:
        """注册一个页面到 Pivot + QStackedWidget"""
        widget.setObjectName(key)
        self._actionStack.addWidget(widget)
        self._actionPivot.addItem(
            routeKey=key,
            text=text,
            onClick=lambda: self._actionStack.setCurrentWidget(widget),
        )

    def _onActionPageChanged(self, index: int) -> None:
        widget = self._actionStack.widget(index)
        self._actionPivot.setCurrentItem(widget.objectName())

    def _buildConfigPage(self) -> QWidget:
        """配置页：使用 GroupHeaderCardWidget"""
        cfgPage = QWidget()
        cfgPage.setObjectName("cfgPage")
        cfgLayout = QVBoxLayout(cfgPage)
        cfgLayout.setContentsMargins(0, 6, 0, 0)
        cfgLayout.setSpacing(10)

        card = GroupHeaderCardWidget(cfgPage)
        card.setTitle("节点配置")
        card.setBorderRadius(8)

        self._cfgTeammate = ComboBox(cfgPage)
        self._cfgTeammate.addItems(["开启", "关闭"])
        self._cfgTeammate.setCurrentText(_DEFAULT_TEAMMATE_TEXT)
        self._cfgTeammate.setFixedWidth(120)
        card.addGroup(FIF.PEOPLE, "补齐队友", "是否自动补满队伍", self._cfgTeammate)

        self._cfgWeapon = ComboBox(cfgPage)
        self._cfgWeapon.addItems(_WEAPONS)
        self._cfgWeapon.setCurrentText(_DEFAULT_WEAPON)
        self._cfgWeapon.setFixedWidth(160)
        card.addGroup(FIF.GAME, "武器配置", "推送武器模板到节点", self._cfgWeapon)

        self._cfgLevel = SpinBox(cfgPage)
        self._cfgLevel.setRange(1, 50)
        self._cfgLevel.setValue(_DEFAULT_LEVEL)
        self._cfgLevel.setFixedWidth(120)
        card.addGroup(FIF.FLAG, "下号等级", "达标后自动换号", self._cfgLevel)

        self._cfgLoot = SpinBox(cfgPage)
        self._cfgLoot.setRange(0, 999)
        self._cfgLoot.setValue(_DEFAULT_LOOT)
        self._cfgLoot.setFixedWidth(160)
        group = card.addGroup(FIF.SHOPPING_CART, "舔包次数", "每局舔包上限", self._cfgLoot)
        group.setSeparatorVisible(True)

        # 底部工具栏
        bottomLayout = QHBoxLayout()
        bottomLayout.setContentsMargins(24, 15, 24, 20)
        bottomLayout.setSpacing(10)
        hintLabel = CaptionLabel("选中节点自动回填，未选中时作用全部在线节点")
        bottomLayout.addWidget(hintLabel, 0, Qt.AlignmentFlag.AlignLeft)
        bottomLayout.addStretch(1)
        btnPush = PrimaryPushButton(FIF.SYNC, "下发配置")
        btnPush.setObjectName("btnPushConfig")
        btnPush.setFixedHeight(36)
        btnPush.clicked.connect(self._pushConfigToNodes)
        bottomLayout.addWidget(btnPush, 0, Qt.AlignmentFlag.AlignRight)
        card.vBoxLayout.addLayout(bottomLayout)

        cfgLayout.addWidget(card)
        cfgLayout.addStretch()

        return cfgPage

    def _buildTokenPage(self) -> QWidget:
        """验证码页：API Key 输入 + 余额查询 + 持久化 + 一键下发到 token.txt"""
        page = QWidget()
        page.setObjectName("tokenPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(10)

        card = GroupHeaderCardWidget(page)
        card.setTitle("验证码管理")
        card.setBorderRadius(8)

        # 充值链接
        link = HyperlinkLabel(page)
        link.setText("前往充值")
        link.setUrl("https://ai.xinyuocr.xyz/home")
        card.addGroup(FIF.LINK, "充值链接", "点击右侧链接前往充值页面", link)

        # API Key 输入
        self._tokenInput = LineEdit(page)
        self._tokenInput.setPlaceholderText("粘贴你的 API Key")
        self._tokenInput.setClearButtonEnabled(True)
        self._tokenInput.setFixedWidth(220)
        # 从配置加载已保存的 Key
        saved_key = self._pool.get_config("api_key")
        if saved_key:
            self._tokenInput.setText(saved_key)
        card.addGroup(FIF.FINGERPRINT, "API Key", "充值后复制 Key 粘贴到此处", self._tokenInput)

        # 余额显示（品字型布局）+ 查询按钮
        balanceWidget = QWidget(page)
        outerLayout = QHBoxLayout(balanceWidget)
        outerLayout.setContentsMargins(0, 0, 0, 0)
        outerLayout.setSpacing(12)

        # 左侧品字型余额
        balanceBox = QVBoxLayout()
        balanceBox.setSpacing(2)
        self._balanceTotalLabel = QLabel("¥--", balanceWidget)
        self._balanceTotalLabel.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #333;"
        )
        balanceBox.addWidget(self._balanceTotalLabel)

        detailRow = QHBoxLayout()
        detailRow.setSpacing(12)
        self._balanceMoneyLabel = CaptionLabel("付费 ¥--", balanceWidget)
        self._balanceFreeLabel = CaptionLabel("积分 ¥--", balanceWidget)
        for lbl in (self._balanceMoneyLabel, self._balanceFreeLabel):
            lbl.setStyleSheet("color: #888; font-size: 12px;")
        detailRow.addWidget(self._balanceMoneyLabel)
        detailRow.addWidget(self._balanceFreeLabel)
        detailRow.addStretch()
        balanceBox.addLayout(detailRow)
        outerLayout.addLayout(balanceBox)

        outerLayout.addStretch()
        btnQuery = PushButton(FIF.SYNC, "查询", balanceWidget)
        btnQuery.setFixedHeight(30)
        btnQuery.setFixedWidth(80)
        btnQuery.clicked.connect(self._queryBalance)
        outerLayout.addWidget(btnQuery)
        card.addGroup(FIF.MARKET, "账户余额", "实时查询验证码识别余额", balanceWidget)

        # 底部工具栏
        bottomLayout = QHBoxLayout()
        bottomLayout.setContentsMargins(24, 15, 24, 20)
        bottomLayout.setSpacing(10)
        hintLabel = CaptionLabel("保存后下发到选中/全部在线节点的 token.txt")
        bottomLayout.addWidget(hintLabel, 0, Qt.AlignmentFlag.AlignLeft)
        bottomLayout.addStretch(1)

        btnSave = PushButton(FIF.SAVE, "保存", page)
        btnSave.setFixedHeight(36)
        btnSave.clicked.connect(self._saveApiKey)
        bottomLayout.addWidget(btnSave)

        btnPush = PrimaryPushButton(FIF.SEND, "保存并下发")
        btnPush.setObjectName("btnPushToken")
        btnPush.setFixedHeight(36)
        btnPush.clicked.connect(self._pushTokenToNodes)
        bottomLayout.addWidget(btnPush)

        card.vBoxLayout.addLayout(bottomLayout)

        layout.addWidget(card)
        layout.addStretch()

        # 有 Key 时自动查询余额
        if saved_key:
            QTimer.singleShot(500, self._queryBalance)

        return page

    def _saveApiKey(self) -> None:
        """保存 API Key 到本地配置"""
        key = self._tokenInput.text().strip()
        if not key:
            InfoBar.warning(
                "提示", "请先输入 API Key",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        self._pool.set_config("api_key", key)
        InfoBar.success(
            "已保存", "API Key 已保存到本地配置",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )
        # 保存后自动查询余额
        self._queryBalance()

    def _pushTokenToNodes(self) -> None:
        """保存 API Key 并下发到选中/全部在线节点的 token.txt"""
        key = self._tokenInput.text().strip()
        if not key:
            InfoBar.warning(
                "提示", "请先输入 API Key",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        # 先保存到本地
        self._pool.set_config("api_key", key)
        # 再下发到节点
        ips, selected = self._getTargetIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        if not selected and not self._confirmDangerous(
            "下发 API Key",
            f"未选中节点，将对全部 {len(ips)} 个在线节点下发，是否继续？",
        ):
            return
        self._tcp.broadcast(ips, TcpCommand.EXT_SET_CONFIG, f"token.txt|{key}")
        scope = f"{len(ips)} 个{'选中' if selected else '在线'}节点"
        self._nm.add_history("下发 API Key", scope)
        InfoBar.success(
            "已下发", f"API Key 已保存并发送到 {scope}",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _queryBalance(self) -> None:
        """后台查询验证码余额并更新显示"""
        key = self._tokenInput.text().strip()
        if not key:
            self._balanceTotalLabel.setText("请先输入 Key")
            self._balanceTotalLabel.setStyleSheet("font-size: 20px; font-weight: bold; color: #999;")
            return
        self._balanceTotalLabel.setText("查询中...")
        self._balanceTotalLabel.setStyleSheet("font-size: 20px; font-weight: bold; color: gray;")

        worker = _BalanceWorker(key, parent=self)
        worker.result_ready.connect(self._onBalanceResult)
        worker.finished.connect(worker.deleteLater)
        self._balance_worker = worker  # 防止 GC
        worker.start()

    def _onBalanceResult(self, total: float, money: float, free: float, error: str) -> None:
        """余额查询结果回调（主线程）"""
        if error:
            self._balanceTotalLabel.setText(error)
            self._balanceTotalLabel.setStyleSheet("font-size: 14px; font-weight: bold; color: #c62828;")
            self._balanceMoneyLabel.setText("")
            self._balanceFreeLabel.setText("")
            return
        if total > 10:
            color = "#2e7d32"
        elif total > 2:
            color = "#e65100"
        else:
            color = "#c62828"
        self._balanceTotalLabel.setText(f"¥{total:.2f}")
        self._balanceTotalLabel.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {color};")
        self._balanceMoneyLabel.setText(f"付费 ¥{money:.2f}")
        self._balanceFreeLabel.setText(f"积分 ¥{free:.2f}")

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

    def _scheduleRefreshTable(self, name: str = "") -> None:
        """防抖：100ms 内的多次信号合并为一次刷新"""
        if name:
            self._pending_updates.add(name)
        if not self._tableRefreshTimer.isActive():
            self._tableRefreshTimer.start()

    def _refreshNodeTable(self) -> None:
        """增量更新节点表格：只更新变化的行，新增/删除时才重建"""
        # 更新分组下拉选项
        groups = self._nm.groups
        current = self._groupCombo.currentText()
        self._groupCombo.blockSignals(True)
        self._groupCombo.clear()
        self._groupCombo.addItem("全部")
        for g in groups:
            self._groupCombo.addItem(g)
        idx = self._groupCombo.findText(current)
        self._groupCombo.setCurrentIndex(max(idx, 0))
        self._groupCombo.blockSignals(False)

        # 按分组筛选
        group_filter = self._groupCombo.currentText()
        nodes = list(self._nm.nodes.values())
        if group_filter != "全部":
            nodes = [n for n in nodes if n.group == group_filter]

        visible_names = {n.machine_name for n in nodes}
        table_names = set(self._row_map.keys())
        pending = self._pending_updates.copy()
        self._pending_updates.clear()

        # 结构变化（新增/删除节点）→ 全量重建
        if visible_names != table_names:
            self.table.setUpdatesEnabled(False)
            self.table.setSortingEnabled(False)
            self.table.setRowCount(len(nodes))
            for row, node in enumerate(nodes):
                self._setRowData(row, node)
            self.table.setSortingEnabled(True)
            self.table.setUpdatesEnabled(True)
            self._rebuildRowMap()
            return

        # 结构不变 → 只更新有变化的行
        for name in pending:
            row = self._row_map.get(name)
            if row is None:
                continue
            node = self._nm.nodes.get(name)
            if node is None:
                continue
            self._setRowData(row, node)

    def _setRowData(self, row: int, node) -> None:
        texts = [
            "",  # col 0 状态图标
            node.machine_name,
            node.ip,
            node.current_account or "--",
            str(node.level) if node.level else "--",
            node.jin_bi if node.jin_bi != "0" else "--",
            self._format_elapsed(node.elapsed),
            node.game_state if node.game_state else node.status,
            f"{node.cpu_percent:.0f}%",
            f"{node.mem_percent:.0f}%",
            node.slave_version or "--",
            self._teammate_fill_display(node.teammate_fill),
            node.weapon_config or "--",
            node.level_threshold or "--",
            node.loot_count or "--",
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

        # 文本列：仅更新变化的单元格
        for col in range(1, len(texts)):
            text = texts[col]
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
            elif item.text() != text:
                item.setText(text)

        # 运行状态列着色（仅变化时）
        state_item = self.table.item(row, 7)
        if state_item:
            color = _STATUS_COLORS.get(node.status, _STATUS_COLOR_DEFAULT)
            if state_item.foreground().color() != color:
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
        btn = self._actionBtns[4]
        count = p.completed_count
        btn.setText(f"提取合格出货 ({count})" if count else "提取合格出货")

    def _refreshAccountTable(self) -> None:
        """从 AccountDB 刷新账号表格"""
        accounts = self._pool.get_all_accounts()
        _MASK = "••••••••"
        self.accountTable.setUpdatesEnabled(False)
        self.accountTable.setRowCount(len(accounts))
        for row, acc in enumerate(accounts):
            vals = [
                acc.username,
                _MASK,
                acc.bind_email,
                _MASK if acc.bind_email_password else "",
                acc.status.value,
                acc.assigned_machine,
                str(acc.level) if acc.level else "",
                acc.jin_bi if acc.jin_bi != "0" else "",
                acc.created_at.strftime("%m-%d %H:%M") if acc.created_at else "",
                acc.completed_at.strftime("%m-%d %H:%M") if acc.completed_at else "",
            ]
            for col, text in enumerate(vals):
                item = self.accountTable.item(row, col)
                if item is None:
                    item = QTableWidgetItem(text)
                    self.accountTable.setItem(row, col, item)
                else:
                    item.setText(text)
            # 状态列着色
            status_item = self.accountTable.item(row, 4)
            if status_item:
                color = _ACCOUNT_STATUS_COLORS.get(acc.status.value)
                if color:
                    status_item.setForeground(color)
        self.accountTable.setUpdatesEnabled(True)

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
                inserted, skipped = self._pool.load_from_text(text)
                msg = f"已导入 {inserted} 个"
                if skipped:
                    msg += f"，跳过 {skipped} 个重复"
                InfoBar.success(
                    "导入成功", msg,
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

    def _resetConfigPanel(self) -> None:
        """恢复配置面板默认值，避免节点切换后残留旧值。"""
        self._cfgTeammate.setCurrentText(_DEFAULT_TEAMMATE_TEXT)
        self._cfgWeapon.setCurrentText(_DEFAULT_WEAPON)
        self._cfgLevel.setValue(_DEFAULT_LEVEL)
        self._cfgLoot.setValue(_DEFAULT_LOOT)

    def _confirmDangerous(self, title: str, content: str) -> bool:
        """危险操作二次确认对话框"""
        dlg = MessageBox(title, content, self.window())
        dlg.yesButton.setText("确认执行")
        dlg.cancelButton.setText("取消")
        return bool(dlg.exec())

    def _extractCompleted(self) -> None:
        """提取已完成账号 → 导出文件（带时间戳）+ 标记已取号"""
        if self._pool.completed_count == 0:
            InfoBar.warning(
                "提示", "没有已完成的账号",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "提取已完成账号", f"提取账号_{ts}.txt", "Text (*.txt)",
        )
        if not path:
            return
        try:
            text = self._pool.export_completed(mark_fetched=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            InfoBar.error(
                "提取失败", str(e),
                parent=self, position=InfoBarPosition.TOP, duration=5000,
            )
            return
        exported = len(text.splitlines()) - 1
        InfoBar.success(
            "提取成功", f"已提取 {exported} 个账号，状态已标记为已取号",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _exportAll(self) -> None:
        """导出所有账号（不改变状态）"""
        if self._pool.total_count == 0:
            InfoBar.warning(
                "提示", "没有账号数据",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出所有账号", f"全部账号_{ts}.txt", "Text (*.txt)",
        )
        if not path:
            return
        try:
            text = self._pool.export_all()
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            InfoBar.error(
                "导出失败", str(e),
                parent=self, position=InfoBarPosition.TOP, duration=5000,
            )
            return
        exported = len(text.splitlines()) - 1
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

    def _cleanStandaloneAccounts(self) -> None:
        """清理单机账号：删除 accounts.txt.imported 和 accounts.json"""
        ips, selected = self._getTargetIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        scope = f"{len(ips)} 个{'选中' if selected else '在线'}节点"
        if not self._confirmDangerous(
            "清理单机账号",
            f"即将删除 {scope} 的 accounts.json 和 accounts.txt.imported，此操作不可恢复",
        ):
            return
        payload = "accounts.txt.imported|accounts.json"
        self._tcp.broadcast(ips, TcpCommand.DELETE_FILE, payload)
        self._nm.add_history("清理单机账号", scope)
        InfoBar.success(
            "已清理", f"清理指令已发送到 {scope}",
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
        if not selected and not self._confirmDangerous(
            "确认操作",
            f"未选中节点，将对全部 {len(ips)} 个在线节点执行，是否继续？",
        ):
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
        if not selected and not self._confirmDangerous(
            "确认操作",
            f"未选中节点，将对全部 {len(ips)} 个在线节点执行，是否继续？",
        ):
            return
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
        if not selected and not self._confirmDangerous(
            "确认操作",
            f"未选中节点，将对全部 {len(ips)} 个在线节点执行，是否继续？",
        ):
            return
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

    def _oneClickStart(self) -> None:
        """一键分发文件：下发配置到节点"""
        ips, selected = self._getTargetIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        if not selected and not self._confirmDangerous(
            "一键分发文件",
            f"未选中节点，将对全部 {len(ips)} 个在线节点下发配置，是否继续？",
        ):
            return
        teammate = "1" if self._cfgTeammate.currentText() == "开启" else "0"
        weapon = self._cfgWeapon.currentText()
        level = str(self._cfgLevel.value())
        loot = str(self._cfgLoot.value())
        for filename, content in [
            ("补齐队友配置.txt", teammate),
            ("武器配置.txt", weapon),
            ("下号等级.txt", level),
            ("舔包次数.txt", loot),
        ]:
            self._tcp.broadcast(
                ips, TcpCommand.EXT_SET_CONFIG, f"{filename}|{content}",
            )
        scope = f"{len(ips)} 个{'选中' if selected else '在线'}节点"
        self._nm.add_history(
            "一键分发文件", scope,
            f"队友={teammate} 武器={weapon} 等级={level} 舔包={loot}",
        )
        InfoBar.success(
            "已分发", f"配置已发送到 {scope}",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _onNodeOffline(self, name: str) -> None:
        """节点离线通知（5s 内超过 5 个时汇总显示）"""
        self._offline_batch.append(name)
        if not self._offlineBatchTimer.isActive():
            self._offlineBatchTimer.start()
        if len(self._offline_batch) <= 5:
            InfoBar.warning(
                "节点离线", f"{name} 已离线",
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )

    def _flushOfflineBatch(self) -> None:
        """离线通知限频：批量汇总"""
        count = len(self._offline_batch)
        if count > 5:
            InfoBar.warning(
                "批量离线", f"{count} 个节点已离线",
                parent=self, position=InfoBarPosition.TOP, duration=5000,
            )
        self._offline_batch.clear()

    def _onGroupFilterChanged(self, _text: str) -> None:
        """分组筛选变更时刷新节点表格"""
        self._refreshNodeTable()

    @staticmethod
    def _format_elapsed(val: str) -> str:
        """将秒数转为人类可读格式: 80537 → 22h23m"""
        if not val or val == "0":
            return "--"
        try:
            total_sec = int(val)
        except ValueError:
            return val
        if total_sec < 60:
            return f"{total_sec}s"
        total_min = total_sec // 60
        hours, mins = divmod(total_min, 60)
        if hours:
            return f"{hours}h{mins:02d}m"
        return f"{mins}m"

    @staticmethod
    def _teammate_fill_display(val: str) -> str:
        match val.strip().lstrip("\ufeff"):
            case "1":
                return "开启"
            case "0":
                return "关闭"
            case v:
                return v or "--"

    def _pushConfigToNodes(self) -> None:
        """下发配置到选中/全部在线节点"""
        ips, selected = self._getTargetIPs()
        if not ips:
            InfoBar.warning(
                "提示", "没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        if not selected and not self._confirmDangerous(
            "下发配置",
            f"未选中节点，将对全部 {len(ips)} 个在线节点下发配置，是否继续？",
        ):
            return
        # 构建配置值
        teammate = "1" if self._cfgTeammate.currentText() == "开启" else "0"
        weapon = self._cfgWeapon.currentText()
        level = str(self._cfgLevel.value())
        loot = str(self._cfgLoot.value())
        configs = [
            ("补齐队友配置.txt", teammate),
            ("武器配置.txt", weapon),
            ("下号等级.txt", level),
            ("舔包次数.txt", loot),
        ]
        for filename, content in configs:
            payload = f"{filename}|{content}"
            self._tcp.broadcast(ips, TcpCommand.EXT_SET_CONFIG, payload)
        scope = f"{len(ips)} 个{'选中' if selected else '在线'}节点"
        self._nm.add_history(
            "下发配置", scope,
            f"队友={teammate} 武器={weapon} 等级={level} 舔包={loot}",
        )
        InfoBar.success(
            "已下发", f"配置已发送到 {scope}",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _onNodeSelectionChanged(self) -> None:
        """节点表格选中变化时，回填配置面板"""
        selected = self.table.selectionModel().selectedRows()
        if len(selected) != 1:
            return
        row = selected[0].row()
        name_item = self.table.item(row, 1)
        if not name_item:
            return
        name = name_item.text()
        node = self._nm.nodes.get(name)
        if not node:
            return
        self._resetConfigPanel()
        if node.teammate_fill == "1":
            self._cfgTeammate.setCurrentText("开启")
        elif node.teammate_fill == "0":
            self._cfgTeammate.setCurrentText(_DEFAULT_TEAMMATE_TEXT)
        if node.weapon_config:
            idx = self._cfgWeapon.findText(node.weapon_config)
            if idx >= 0:
                self._cfgWeapon.setCurrentIndex(idx)
        if node.level_threshold and node.level_threshold.isdigit():
            self._cfgLevel.setValue(int(node.level_threshold))
        if node.loot_count and node.loot_count.isdigit():
            self._cfgLoot.setValue(int(node.loot_count))
