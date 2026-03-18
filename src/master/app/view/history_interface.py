"""操作历史页面 — 指令记录追踪"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    PushButton,
    ScrollArea,
    SubtitleLabel,
    TableWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from master.app.core.node_manager import NodeManager

_HEADERS = ["时间", "操作类型", "目标节点", "详情", "结果"]


class HistoryInterface(ScrollArea):
    def __init__(self, node_manager: NodeManager, parent=None):
        super().__init__(parent)
        self.setObjectName("historyInterface")
        self._nm = node_manager

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.mainLayout = QVBoxLayout(self.view)
        self.mainLayout.setContentsMargins(24, 24, 24, 24)
        self.mainLayout.setSpacing(16)

        # ── 标题 ──
        titleLayout = QHBoxLayout()
        titleLayout.addWidget(SubtitleLabel("操作历史", self))
        titleLayout.addStretch()

        self.typeFilter = ComboBox(self)
        self.typeFilter.addItems(["全部", "STARTEXE", "STOPEXE", "REBOOTPC", "UPDATEKEY", "分发卡密"])
        self.typeFilter.setFixedWidth(140)
        titleLayout.addWidget(self.typeFilter)

        self.btnClear = PushButton(FIF.DELETE, "清空历史", self)
        titleLayout.addWidget(self.btnClear)
        self.mainLayout.addLayout(titleLayout)

        # ── 表格 ──
        self.table = TableWidget(self)
        self.table.setColumnCount(len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.mainLayout.addWidget(self.table)

        # ── 空状态 ──
        self.emptyLabel = QWidget(self)
        emptyLayout = QVBoxLayout(self.emptyLabel)
        emptyLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emptyLayout.addWidget(SubtitleLabel("暂无操作记录"))
        emptyLayout.addWidget(BodyLabel("对节点执行操作后，记录将显示在这里"))
        self.mainLayout.addWidget(self.emptyLabel)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ── 信号 ──
        self.typeFilter.currentTextChanged.connect(self._refreshTable)
        self.btnClear.clicked.connect(self._clearHistory)

        # 定时刷新（每5秒检查新记录）
        self._lastCount = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._checkForUpdates)
        self._timer.start(5000)

    def _checkForUpdates(self) -> None:
        if len(self._nm.history) != self._lastCount:
            self._lastCount = len(self._nm.history)
            self._refreshTable()

    def _refreshTable(self) -> None:
        type_filter = self.typeFilter.currentText()
        records = self._nm.history[::-1]  # newest first
        if type_filter != "全部":
            records = [r for r in records if r.op_type == type_filter]

        self.table.setRowCount(len(records))
        for i, rec in enumerate(records):
            self.table.setItem(i, 0, QTableWidgetItem(rec.timestamp.strftime("%Y-%m-%d %H:%M:%S")))
            self.table.setItem(i, 1, QTableWidgetItem(rec.op_type))
            self.table.setItem(i, 2, QTableWidgetItem(rec.target))
            self.table.setItem(i, 3, QTableWidgetItem(rec.detail))
            self.table.setItem(i, 4, QTableWidgetItem(rec.result))

        self.emptyLabel.setVisible(len(records) == 0)
        self.table.setVisible(len(records) > 0)

    def _clearHistory(self) -> None:
        self._nm.history.clear()
        self._lastCount = 0
        self._refreshTable()
