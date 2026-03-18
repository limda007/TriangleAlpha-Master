"""账号管理页面 — 导入/导出/状态追踪"""
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
    BodyLabel,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    MenuAnimationType,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    ScrollArea,
    SubtitleLabel,
    TableWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from master.app.components.stat_card import StatCard
from master.app.core.account_pool import AccountPool

_HEADERS = ["账号", "密码", "邮箱", "邮箱密码", "状态", "分配机器", "等级", "完成时间"]
_MASK = "••••••••"
_SECRET_COLS = {1, 3}  # password + email password columns


class AccountInterface(ScrollArea):
    def __init__(self, account_pool: AccountPool, parent=None):
        super().__init__(parent)
        self.setObjectName("accountInterface")
        self._pool = account_pool
        self._revealed: set[int] = set()  # rows with password revealed

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.mainLayout = QVBoxLayout(self.view)
        self.mainLayout.setContentsMargins(24, 24, 24, 24)
        self.mainLayout.setSpacing(16)

        # ── 统计卡片 ──
        statsLayout = QHBoxLayout()
        statsLayout.setSpacing(12)
        self.totalCard = StatCard("总数", "0")
        self.availableCard = StatCard("可用", "0")
        self.inUseCard = StatCard("使用中", "0")
        self.completedCard = StatCard("已完成", "0")
        for card in (self.totalCard, self.availableCard, self.inUseCard, self.completedCard):
            statsLayout.addWidget(card)
        self.mainLayout.addLayout(statsLayout)

        # ── 工具栏 ──
        toolLayout = QHBoxLayout()
        self.btnImport = PrimaryPushButton(FIF.FOLDER_ADD, "导入账号", self)
        self.btnExport = PushButton(FIF.SAVE, "导出已完成", self)
        self.btnClear = PushButton(FIF.DELETE, "清空", self)
        self.statusFilter = ComboBox(self)
        self.statusFilter.addItems(["全部", "空闲", "使用中", "已完成"])
        self.statusFilter.setFixedWidth(100)
        toolLayout.addWidget(self.btnImport)
        toolLayout.addWidget(self.btnExport)
        toolLayout.addWidget(self.btnClear)
        toolLayout.addStretch()
        toolLayout.addWidget(self.statusFilter)
        self.mainLayout.addLayout(toolLayout)

        # ── 表格 ──
        self.table = TableWidget(self)
        self.table.setColumnCount(len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(_HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellClicked.connect(self._onCellClicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._showContextMenu)
        self.mainLayout.addWidget(self.table)

        # ── 空状态提示 ──
        self.emptyLabel = QWidget(self)
        emptyLayout = QVBoxLayout(self.emptyLabel)
        emptyLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emptyLayout.addWidget(SubtitleLabel("暂无账号数据"))
        tip = BodyLabel("点击「导入账号」加载 accounts.txt（格式: 账号----密码----邮箱----邮箱密码----[备注]）")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emptyLayout.addWidget(tip)
        self.mainLayout.addWidget(self.emptyLabel)
        self.emptyLabel.setVisible(True)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ── 信号 ──
        self._pool.pool_changed.connect(self._refreshTable)
        self.btnImport.clicked.connect(self._importAccounts)
        self.btnExport.clicked.connect(self._exportCompleted)
        self.btnClear.clicked.connect(self._clearAccounts)
        self.statusFilter.currentTextChanged.connect(self._applyFilter)

    def _refreshTable(self) -> None:
        self._revealed.clear()
        accounts = self._pool.accounts
        self.table.setRowCount(len(accounts))
        for row, acc in enumerate(accounts):
            self.table.setItem(row, 0, QTableWidgetItem(acc.username))
            # 密码列（掩码）
            pwd_item = QTableWidgetItem(_MASK)
            pwd_item.setData(Qt.ItemDataRole.UserRole, acc.password)
            self.table.setItem(row, 1, pwd_item)
            self.table.setItem(row, 2, QTableWidgetItem(acc.bind_email))
            # 邮箱密码列（掩码）
            epwd_item = QTableWidgetItem(_MASK if acc.bind_email_password else "")
            epwd_item.setData(Qt.ItemDataRole.UserRole, acc.bind_email_password)
            self.table.setItem(row, 3, epwd_item)
            self.table.setItem(row, 4, QTableWidgetItem(acc.status.value))
            self.table.setItem(row, 5, QTableWidgetItem(acc.assigned_machine))
            self.table.setItem(row, 6, QTableWidgetItem(str(acc.level) if acc.level else ""))
            time_str = acc.completed_at.strftime("%m-%d %H:%M") if acc.completed_at else ""
            self.table.setItem(row, 7, QTableWidgetItem(time_str))
        self._refreshStats()
        self._applyFilter()
        self.emptyLabel.setVisible(len(accounts) == 0)
        self.table.setVisible(len(accounts) > 0)

    def _refreshStats(self) -> None:
        self.totalCard.setValue(str(self._pool.total_count))
        self.availableCard.setValue(str(self._pool.available_count))
        self.inUseCard.setValue(str(self._pool.in_use_count))
        self.completedCard.setValue(str(self._pool.completed_count))

    def _onCellClicked(self, row: int, col: int) -> None:
        if col not in _SECRET_COLS:
            return
        item = self.table.item(row, col)
        if not item:
            return
        real_val = str(item.data(Qt.ItemDataRole.UserRole))
        if row in self._revealed:
            item.setText(_MASK)
            self._revealed.discard(row)
        else:
            item.setText(real_val)
            self._revealed.add(row)

    def _applyFilter(self) -> None:
        status_text = self.statusFilter.currentText()
        status_map = {"空闲": "空闲", "使用中": "使用中", "已完成": "已完成"}
        target = status_map.get(status_text)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 4)  # 状态列
            if target is None or (item and item.text() == target):
                self.table.setRowHidden(row, False)
            else:
                self.table.setRowHidden(row, True)

    def _importAccounts(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入账号文件", "", "Text (*.txt);;CSV (*.csv);;All (*)"
        )
        if not path:
            return
        self._pool.load_from_file(path)
        InfoBar.success(
            "导入成功", f"已加载 {self._pool.total_count} 个账号",
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _exportCompleted(self) -> None:
        if self._pool.completed_count == 0:
            InfoBar.warning("提示", "没有已完成的账号", parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出已完成账号", "finished_accounts.txt", "Text (*.txt)"
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

    def _clearAccounts(self) -> None:
        if self._pool.total_count == 0:
            return
        dlg = MessageBox("确认清空", f"确定要清空全部 {self._pool.total_count} 个账号吗？此操作不可撤销。", self)
        if not dlg.exec():
            return
        self._pool.load_from_text("")
        InfoBar.info("已清空", "账号池已清空", parent=self,
                     position=InfoBarPosition.TOP, duration=2000)

    def _showContextMenu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        menu = RoundMenu(parent=self.table)
        # 复制账号
        acc_item = self.table.item(row, 0)
        if acc_item:
            menu.addAction(Action(FIF.COPY, "复制账号", triggered=lambda: self._copyToClipboard(acc_item.text())))
        # 复制密码
        pwd_item = self.table.item(row, 1)
        if pwd_item:
            real_pwd = str(pwd_item.data(Qt.ItemDataRole.UserRole))
            menu.addAction(Action(FIF.COPY, "复制密码", triggered=lambda: self._copyToClipboard(real_pwd)))
        menu.exec(self.table.viewport().mapToGlobal(pos), aniType=MenuAnimationType.NONE)

    def _copyToClipboard(self, text: str) -> None:
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
        InfoBar.success("已复制", "", parent=self, position=InfoBarPosition.TOP, duration=1500)
