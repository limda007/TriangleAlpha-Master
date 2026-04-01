"""账号管理页面 — 导入/导出/状态追踪"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCloseEvent, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
from master.app.core.account_db import AccountDB

_HEADERS = ["账号", "密码", "邮箱", "邮箱密码", "状态", "分配机器", "等级", "金币", "登录时间", "完成时间"]
_MASK = "••••••••"
_SECRET_COLS = {1, 3}  # password + email password columns
_STATUS_COL = 4

_STATUS_COLORS: dict[str, tuple[str, str]] = {
    "空闲中": ("#e8f5e9", "#2e7d32"),
    "运行中": ("#e3f2fd", "#1565c0"),
    "已完成": ("#fff3e0", "#e65100"),
    "已取号": ("#f3e5f5", "#6a1b9a"),
    "已封禁": ("#ffebee", "#c62828"),
}
_COLUMN_WIDTHS = {
    1: 92,
    3: 108,
    4: 86,
    5: 140,
    6: 64,
    7: 96,
    8: 152,
    9: 120,
}


class AccountInterface(ScrollArea):
    def __init__(self, account_pool: AccountDB, parent=None):
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
        self.inUseCard = StatCard("运行中", "0")
        self.completedCard = StatCard("已完成", "0")
        for card in (self.totalCard, self.availableCard, self.inUseCard, self.completedCard):
            statsLayout.addWidget(card)
        self.mainLayout.addLayout(statsLayout)

        # ── 工具栏 ──
        toolLayout = QHBoxLayout()
        self.btnImport = PrimaryPushButton(FIF.FOLDER_ADD, "导入账号", self)
        self.btnExtract = PushButton(FIF.COMPLETED, "提取账号", self)
        self.btnExportAll = PushButton(FIF.SAVE, "导出所有", self)
        self.btnClear = PushButton(FIF.DELETE, "清空", self)
        self.statusFilter = ComboBox(self)
        self.statusFilter.addItems(["全部", "空闲中", "运行中", "已完成", "已取号"])
        self.statusFilter.setFixedWidth(100)
        toolLayout.addWidget(self.btnImport)
        toolLayout.addWidget(self.btnExtract)
        toolLayout.addWidget(self.btnExportAll)
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
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(64)
        header.setStretchLastSection(False)
        for col, width in _COLUMN_WIDTHS.items():
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, width)
        self.table.verticalHeader().setDefaultSectionSize(30)
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
        self._refreshTimer = QTimer(self)
        self._refreshTimer.setSingleShot(True)
        self._refreshTimer.setInterval(100)
        self._refreshTimer.timeout.connect(self._refreshTable)
        self._pool.pool_changed.connect(self._scheduleRefreshTable)
        self.btnImport.clicked.connect(self._importAccounts)
        self.btnExtract.clicked.connect(self._extractCompleted)
        self.btnExportAll.clicked.connect(self._exportAll)
        self.btnClear.clicked.connect(self._clearAccounts)
        self.statusFilter.currentTextChanged.connect(self._applyFilter)

        # 首次加载
        self._refreshTable()

    def _scheduleRefreshTable(self) -> None:
        if not self._refreshTimer.isActive():
            self._refreshTimer.start()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._refreshTimer.stop()
        super().closeEvent(event)

    def _refreshTable(self) -> None:
        self._revealed.clear()
        accounts = self._pool.get_all_accounts()
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(accounts))
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
                acc.last_login_at.strftime("%m-%d %H:%M") if acc.last_login_at else "",
                acc.completed_at.strftime("%m-%d %H:%M") if acc.completed_at else "",
            ]
            for col, text in enumerate(vals):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem(text)
                    self.table.setItem(row, col, item)
                else:
                    item.setText(text)
                # 密码列保存真实值
                if col == 1:
                    item.setData(Qt.ItemDataRole.UserRole, acc.password)
                elif col == 3:
                    item.setData(Qt.ItemDataRole.UserRole, acc.bind_email_password)
                # 状态列：扁平圆角标签
                if col == _STATUS_COL:
                    item.setData(Qt.ItemDataRole.UserRole, text)
                    bg, fg = _STATUS_COLORS.get(text, ("#f5f5f5", "#333333"))
                    self.table.removeCellWidget(row, col)
                    item.setText(text)
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                    item.setBackground(QColor(bg))
                    item.setForeground(QColor(fg))
        self.table.setUpdatesEnabled(True)
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
        status_map = {"空闲中": "空闲中", "运行中": "运行中", "已完成": "已完成", "已取号": "已取号"}
        target = status_map.get(status_text)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 4)  # 状态列
            if target is None or (item and item.data(Qt.ItemDataRole.UserRole) == target):
                self.table.setRowHidden(row, False)
            else:
                self.table.setRowHidden(row, True)

    def _importAccounts(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入账号文件", "", "Text (*.txt);;CSV (*.csv);;All (*)"
        )
        if not path:
            return
        inserted, skipped = self._pool.load_from_file(path)
        message = f"已新增 {inserted} 个账号"
        if skipped:
            message += f"，跳过 {skipped} 个重复"
        InfoBar.success(
            "导入成功", message,
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _extractCompleted(self) -> None:
        """提取已完成账号 → 导出文件（带时间戳）+ 标记已取号"""
        if self._pool.completed_count == 0:
            InfoBar.warning("提示", "没有已完成的账号", parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"提取账号_{ts}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "提取已完成账号", default_name, "Text (*.txt)"
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
            InfoBar.warning("提示", "没有账号数据", parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"全部账号_{ts}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出所有账号", default_name, "Text (*.txt)"
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

    def _clearAccounts(self) -> None:
        if self._pool.total_count == 0:
            return
        dlg = MessageBox("确认清空", f"确定要清空全部 {self._pool.total_count} 个账号吗？此操作不可撤销。", self)
        if not dlg.exec():
            return
        self._pool.clear_all()
        InfoBar.info("已清空", "账号池已清空", parent=self,
                     position=InfoBarPosition.TOP, duration=2000)

    def _showContextMenu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        selected_rows = self.table.selectionModel().selectedRows()
        selected_indices = [idx.row() for idx in selected_rows] if selected_rows else [row]
        count = len(selected_indices)

        menu = RoundMenu(parent=self.table)
        # 复制操作（单行）
        acc_item = self.table.item(row, 0)
        if acc_item:
            menu.addAction(Action(FIF.COPY, "复制账号", triggered=lambda: self._copyToClipboard(acc_item.text())))
        pwd_item = self.table.item(row, 1)
        if pwd_item:
            real_pwd = str(pwd_item.data(Qt.ItemDataRole.UserRole))
            menu.addAction(Action(FIF.COPY, "复制密码", triggered=lambda: self._copyToClipboard(real_pwd)))
        email_item = self.table.item(row, 2)
        if email_item and email_item.text():
            menu.addAction(Action(FIF.COPY, "复制邮箱", triggered=lambda: self._copyToClipboard(email_item.text())))
        epwd_item = self.table.item(row, 3)
        if epwd_item:
            real_epwd = str(epwd_item.data(Qt.ItemDataRole.UserRole))
            if real_epwd:
                menu.addAction(Action(FIF.COPY, "复制邮箱密码", triggered=lambda: self._copyToClipboard(real_epwd)))
        menu.addAction(Action(FIF.COPY, "复制整行", triggered=lambda: self._copyFullRow(row)))
        menu.addSeparator()
        # 释放绑定（支持多选）
        releasable = self._getReleasableRows()
        if releasable:
            rc = len(releasable)
            menu.addAction(
                Action(
                    FIF.REMOVE,
                    f"释放绑定 ({rc}行)" if rc > 1 else "释放绑定",
                    triggered=lambda: self._releaseSelectedAccounts(releasable),
                )
            )
        # 删除（支持多选）
        menu.addSeparator()
        label = f"删除选中 ({count}行)" if count > 1 else "删除"
        menu.addAction(
            Action(
                FIF.DELETE,
                label,
                triggered=lambda: self._deleteSelectedAccounts(selected_indices),
            )
        )
        menu.exec(self.table.viewport().mapToGlobal(pos), aniType=MenuAnimationType.NONE)

    def _copyFullRow(self, row: int) -> None:
        """复制整行数据（----分隔格式）"""
        parts = []
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                if col in _SECRET_COLS:
                    parts.append(str(item.data(Qt.ItemDataRole.UserRole)))
                else:
                    parts.append(item.text())
            else:
                parts.append("")
        self._copyToClipboard("----".join(parts))

    def _getReleasableRows(self) -> list[str]:
        """返回选中行中状态为'运行中'的 machine_name 列表"""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return []
        machines = []
        for idx in selected_rows:
            status_item = self.table.item(idx.row(), 4)  # 状态列
            machine_item = self.table.item(idx.row(), 5)  # 分配机器列
            if (
                status_item
                and status_item.data(Qt.ItemDataRole.UserRole) == "运行中"
                and machine_item
                and machine_item.text()
            ):
                machines.append(machine_item.text())
        return machines

    def _releaseSelectedAccounts(self, machines: list[str]) -> None:
        """释放选中行的绑定账号"""
        released = 0
        for m in machines:
            self._pool.release(m)
            released += 1
        if released:
            InfoBar.success(
                "已释放", f"已释放 {released} 个绑定账号",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )

    def _deleteSelectedAccounts(self, rows: list[int]) -> None:
        """删除选中行的账号"""
        usernames = []
        for r in rows:
            item = self.table.item(r, 0)
            if item:
                usernames.append(item.text())
        if not usernames:
            return
        dlg = MessageBox(
            "确认删除",
            f"确定要删除 {len(usernames)} 个账号吗？\n"
            "删除后 slave 同步不会恢复这些账号，手动重新导入可恢复。",
            self,
        )
        if not dlg.exec():
            return
        deleted = self._pool.delete_by_usernames(usernames)
        InfoBar.success(
            "已删除", f"已删除 {deleted} 个账号",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )

    def _copyToClipboard(self, text: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
        InfoBar.success("已复制", "", parent=self, position=InfoBarPosition.TOP, duration=1500)
