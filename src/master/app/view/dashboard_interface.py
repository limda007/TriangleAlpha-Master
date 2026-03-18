"""仪表盘页面 — 全局统计 + 节点概览 + 最近操作"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    ProgressBar,
    ScrollArea,
    SimpleCardWidget,
    SubtitleLabel,
    TableWidget,
)

from master.app.components.stat_card import StatCard
from master.app.core.account_pool import AccountPool
from master.app.core.node_manager import NodeManager


class _StatusRow(QWidget):
    """单行状态指示: 圆点 + 标签 + 数量 + 进度条"""

    def __init__(self, color: str, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        dot = QLabel(f'<span style="color:{color}; font-size:16px;">●</span>', self)
        layout.addWidget(dot)

        self._label = QLabel(label, self)
        self._label.setFixedWidth(40)
        layout.addWidget(self._label)

        self._count = QLabel("0", self)
        self._count.setFixedWidth(30)
        self._count.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self._count)

        self._bar = ProgressBar(self)
        self._bar.setFixedHeight(6)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        layout.addWidget(self._bar, 1)

        self._pct = QLabel("0%", self)
        self._pct.setFixedWidth(40)
        self._pct.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self._pct)

    def update_data(self, count: int, total: int) -> None:
        self._count.setText(str(count))
        pct = round(count * 100 / total) if total > 0 else 0
        self._bar.setValue(pct)
        self._pct.setText(f"{pct}%")


class DashboardInterface(ScrollArea):
    """仪表盘主页面：统计卡片 + 节点状态概览 + 最近操作列表"""

    def __init__(
        self,
        node_manager: NodeManager,
        account_pool: AccountPool,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("dashboardInterface")
        self._nm = node_manager
        self._pool = account_pool
        self._start_time = datetime.now()

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.mainLayout = QVBoxLayout(self.view)
        self.mainLayout.setContentsMargins(24, 24, 24, 24)
        self.mainLayout.setSpacing(16)

        # ── 统计卡片行 ──────────────────────────────────────────
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(12)
        self.onlineCard = StatCard("在线节点", "0")
        self.totalCard = StatCard("总节点", "0")
        self.accountCard = StatCard("可用账号", "0 / 0")
        self.uptimeCard = StatCard("运行时长", "0m")
        for card in (self.onlineCard, self.totalCard, self.accountCard, self.uptimeCard):
            stats_layout.addWidget(card)
        self.mainLayout.addLayout(stats_layout)

        # ── 下方两列 ────────────────────────────────────────────
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(16)

        # 左: 节点状态概览
        status_card = SimpleCardWidget(self)
        status_card.setObjectName("statCard")
        status_card.setMinimumHeight(200)
        status_vbox = QVBoxLayout(status_card)
        status_vbox.setContentsMargins(20, 16, 20, 16)
        status_vbox.setSpacing(8)
        status_title = SubtitleLabel("节点状态概览", status_card)
        status_vbox.addWidget(status_title)
        status_vbox.addSpacing(8)

        self._online_row = _StatusRow("#22c55e", "在线", status_card)
        self._offline_row = _StatusRow("#ef4444", "离线", status_card)
        self._disconn_row = _StatusRow("#6b7280", "断连", status_card)
        status_vbox.addWidget(self._online_row)
        status_vbox.addWidget(self._offline_row)
        status_vbox.addWidget(self._disconn_row)
        status_vbox.addStretch()
        bottom_layout.addWidget(status_card, 1)

        # 右: 最近操作
        history_card = SimpleCardWidget(self)
        history_card.setObjectName("statCard")
        history_card.setMinimumHeight(200)
        hist_vbox = QVBoxLayout(history_card)
        hist_vbox.setContentsMargins(20, 16, 20, 16)
        hist_vbox.setSpacing(8)
        hist_title = SubtitleLabel("最近操作", history_card)
        hist_vbox.addWidget(hist_title)

        self.historyTable = TableWidget(history_card)
        self.historyTable.setColumnCount(3)
        self.historyTable.setHorizontalHeaderLabels(["时间", "操作", "目标"])
        self.historyTable.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.historyTable.setAlternatingRowColors(True)
        self.historyTable.verticalHeader().hide()
        hdr = self.historyTable.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hist_vbox.addWidget(self.historyTable)
        bottom_layout.addWidget(history_card, 1)

        self.mainLayout.addLayout(bottom_layout)
        self.mainLayout.addStretch()

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ── 信号连接 ────────────────────────────────────────────
        self._nm.stats_changed.connect(self._refresh)
        self._nm.node_online.connect(lambda _: self._refresh())
        self._nm.node_offline.connect(lambda _: self._refresh())
        self._pool.pool_changed.connect(self._refresh)

        # 运行时长定时器 — 每分钟刷新一次
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._update_uptime)
        self._uptime_timer.start(60_000)

    # ── 刷新逻辑 ────────────────────────────────────────────────

    def _refresh(self) -> None:
        """刷新所有统计数据和操作记录"""
        online = self._nm.online_count
        total = self._nm.total_count
        offline = sum(1 for n in self._nm.nodes.values() if n.status == "离线")
        disconn = sum(1 for n in self._nm.nodes.values() if n.status == "断连")

        self.onlineCard.setValue(str(online))
        self.totalCard.setValue(str(total))
        self.accountCard.setValue(
            f"{self._pool.available_count} / {self._pool.total_count}"
        )
        self._update_uptime()

        safe_total = max(total, 1)
        self._online_row.update_data(online, safe_total)
        self._offline_row.update_data(offline, safe_total)
        self._disconn_row.update_data(disconn, safe_total)

        self._refresh_history()

    def _update_uptime(self) -> None:
        """更新运行时长显示"""
        delta = datetime.now() - self._start_time
        total_min = int(delta.total_seconds() // 60)
        hours, mins = divmod(total_min, 60)
        if hours > 0:
            self.uptimeCard.setValue(f"{hours}h {mins}m")
        else:
            self.uptimeCard.setValue(f"{mins}m")

    def _refresh_history(self) -> None:
        """刷新最近操作表格（最近 10 条，最新在前）"""
        records = self._nm.history[-10:][::-1]
        self.historyTable.setRowCount(len(records))
        for i, rec in enumerate(records):
            self.historyTable.setItem(
                i, 0, QTableWidgetItem(rec.timestamp.strftime("%H:%M:%S"))
            )
            self.historyTable.setItem(i, 1, QTableWidgetItem(rec.op_type))
            self.historyTable.setItem(i, 2, QTableWidgetItem(rec.target))
