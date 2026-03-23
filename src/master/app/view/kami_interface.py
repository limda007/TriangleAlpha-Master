"""卡密管理页面 — 导入/刷新/分配/删除"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
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
    SearchLineEdit,
    SubtitleLabel,
    TableWidget,
)
from qfluentwidgets import FluentIcon as FIF

from master.app.components.stat_card import StatCard
from master.app.core.kami_client import KamiQueryWorker
from master.app.core.kami_db import KamiDB

_HEADERS = ["卡密", "类型", "关联节点", "剩余天数", "到期日期", "激活时间", "状态", "设备数"]
_STATUS_COL = 6

_STATUS_COLORS: dict[str, tuple[str, str]] = {
    "已激活": ("#e8f5e9", "#2e7d32"),
    "已过期": ("#ffebee", "#c62828"),
    "未使用": ("#f5f5f5", "#757575"),
    "未知": ("#fff3e0", "#e65100"),
}


class KamiInterface(ScrollArea):
    def __init__(
        self,
        kami_db: KamiDB,
        node_manager: object,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("kamiInterface")
        self._kami_db = kami_db
        self._node_manager = node_manager
        self._worker: KamiQueryWorker | None = None

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.mainLayout = QVBoxLayout(self.view)
        self.mainLayout.setContentsMargins(24, 24, 24, 24)
        self.mainLayout.setSpacing(16)

        # ── 统计卡片 ──
        statsLayout = QHBoxLayout()
        statsLayout.setSpacing(12)
        self.totalCard = StatCard("总数", "0")
        self.validCard = StatCard("已激活", "0")
        self.expiredCard = StatCard("已过期", "0")
        self.unusedCard = StatCard("未使用", "0")
        for card in (self.totalCard, self.validCard, self.expiredCard, self.unusedCard):
            statsLayout.addWidget(card)
        self.mainLayout.addLayout(statsLayout)

        # ── 工具栏 ──
        toolLayout = QHBoxLayout()
        self.btnImport = PrimaryPushButton(FIF.ADD, "导入卡密", self)
        self.btnRefresh = PushButton(FIF.SYNC, "刷新状态", self)
        self.btnDelete = PushButton(FIF.DELETE, "删除选中", self)
        self.searchEdit = SearchLineEdit(self)
        self.searchEdit.setPlaceholderText("搜索卡密/节点")
        self.searchEdit.setFixedWidth(200)
        self.statusFilter = ComboBox(self)
        self.statusFilter.addItems(["全部", "已激活", "已过期", "未使用", "未知"])
        self.statusFilter.setFixedWidth(100)
        toolLayout.addWidget(self.btnImport)
        toolLayout.addWidget(self.btnRefresh)
        toolLayout.addWidget(self.btnDelete)
        toolLayout.addStretch()
        toolLayout.addWidget(self.searchEdit)
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
        header.setMinimumSectionSize(100)
        for col in range(1, len(_HEADERS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._showContextMenu)
        self.mainLayout.addWidget(self.table)

        # ── 空状态提示 ──
        self.emptyLabel = QWidget(self)
        emptyLayout = QVBoxLayout(self.emptyLabel)
        emptyLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emptyLayout.addWidget(SubtitleLabel("暂无卡密数据"))
        tip = BodyLabel("点击「导入卡密」添加卡密代码（每行一个）")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emptyLayout.addWidget(tip)
        self.mainLayout.addWidget(self.emptyLabel)
        self.emptyLabel.setVisible(True)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ── 信号 ──
        self._kami_db.kami_changed.connect(self._refreshTable)
        self.btnImport.clicked.connect(self._importKamis)
        self.btnRefresh.clicked.connect(self._refreshFromApi)
        self.btnDelete.clicked.connect(self._deleteSelected)
        self.statusFilter.currentTextChanged.connect(self._applyFilter)
        self.searchEdit.textChanged.connect(self._applyFilter)

        # ── 定时轮询（每小时） ──
        self._pollTimer = QTimer(self)
        self._pollTimer.timeout.connect(self._refreshFromApi)
        self._pollTimer.start(3600_000)

        # 首次加载
        self._refreshTable()

    # ── 表格刷新 ──────────────────────────────────────────

    def _refreshTable(self) -> None:
        kamis = self._kami_db.get_all_kamis()
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(kamis))
        for row, k in enumerate(kamis):
            vals = [
                k.kami_code,
                k.kami_type or "-",
                ", ".join(k.bound_nodes) if k.bound_nodes else "-",
                str(k.remaining_days),
                k.end_date or "-",
                k.activated_at or "-",
                k.status.value,
                f"{k.device_used}/{k.device_total}",
            ]
            for col, text in enumerate(vals):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.table.setItem(row, col, item)
                else:
                    item.setText(text)
                # 存储 kami_id 到第一列
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, k.id)
                # 状态列：彩色标签
                if col == _STATUS_COL:
                    item.setText("")
                    item.setData(Qt.ItemDataRole.UserRole, text)
                    bg, fg = _STATUS_COLORS.get(text, ("#f5f5f5", "#333333"))
                    tag = QLabel(text)
                    tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    tag.setStyleSheet(
                        f"background:{bg}; color:{fg}; border-radius:4px;"
                        " padding:2px 8px; font-size:12px;"
                    )
                    container = QWidget()
                    lay = QHBoxLayout(container)
                    lay.setContentsMargins(4, 2, 4, 2)
                    lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    lay.addWidget(tag)
                    self.table.setCellWidget(row, col, container)
        self.table.setUpdatesEnabled(True)
        self._refreshStats()
        self._applyFilter()
        self.emptyLabel.setVisible(len(kamis) == 0)
        self.table.setVisible(len(kamis) > 0)

    def _refreshStats(self) -> None:
        self.totalCard.setValue(str(self._kami_db.total_count))
        self.validCard.setValue(str(self._kami_db.valid_count))
        self.expiredCard.setValue(str(self._kami_db.expired_count))
        self.unusedCard.setValue(str(self._kami_db.unused_count))

    def _applyFilter(self) -> None:
        status_text = self.statusFilter.currentText()
        search_text = self.searchEdit.text().strip().lower()
        for row in range(self.table.rowCount()):
            show = True
            # 状态过滤
            if status_text != "全部":
                status_item = self.table.item(row, _STATUS_COL)
                if status_item:
                    val = status_item.data(Qt.ItemDataRole.UserRole)
                    if val != status_text:
                        show = False
            # 关键字搜索
            if show and search_text:
                matched = False
                for col in (0, 2):  # 卡密代码、关联节点
                    item = self.table.item(row, col)
                    if item and search_text in item.text().lower():
                        matched = True
                        break
                if not matched:
                    show = False
            self.table.setRowHidden(row, not show)

    # ── 导入卡密 ──────────────────────────────────────────

    def _importKamis(self) -> None:
        if self._worker is not None:
            InfoBar.warning(
                "请稍候", "正在处理中...",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        # 弹出导入对话框
        dlg = MessageBox("导入卡密", "", self)
        # 替换内容区为多行文本编辑
        textEdit = QPlainTextEdit()
        textEdit.setPlaceholderText("每行一个卡密代码，例如:\nABCDE-FGHIJ-KLMNO-PQRST\nXXXXX-YYYYY-ZZZZZ-WWWWW")
        textEdit.setMinimumHeight(200)
        textEdit.setMinimumWidth(400)
        # 插入到对话框布局
        dlg.textLayout.addWidget(textEdit)
        if not dlg.exec():
            return
        text = textEdit.toPlainText().strip()
        if not text:
            return
        kami_codes = [
            line.strip() for line in text.splitlines() if line.strip()
        ]
        if not kami_codes:
            return
        self._startWorker(kami_codes, self._onImportDone)

    def _onImportDone(self, results: list[dict]) -> None:
        # 所有结果都 upsert 到 DB（ok=true → 有效, ok=false → 已过期）
        if not results:
            InfoBar.warning(
                "导入失败", "API 未返回任何结果",
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )
            return
        inserted, updated = self._kami_db.upsert_kamis(results)
        valid_count = sum(1 for r in results if r.get("ok"))
        invalid_count = sum(1 for r in results if not r.get("ok"))
        msg = f"导入 {inserted} 个，更新 {updated} 个"
        if valid_count:
            msg += f"（有效 {valid_count}"
            if invalid_count:
                msg += f"，无效 {invalid_count}"
            msg += "）"
        InfoBar.success(
            "导入完成", msg,
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    # ── 刷新状态 ──────────────────────────────────────────

    def _refreshFromApi(self) -> None:
        if self._worker is not None:
            return
        kami_codes = self._kami_db.get_kami_codes()
        if not kami_codes:
            return
        self._startWorker(kami_codes, self._onRefreshDone)

    def _onRefreshDone(self, results: list[dict]) -> None:
        if results:
            self._kami_db.upsert_kamis(results)

    # ── Worker 管理 ───────────────────────────────────────

    def _startWorker(
        self, kami_codes: list[str],
        on_done: object,
    ) -> None:
        self.btnImport.setEnabled(False)
        self.btnRefresh.setEnabled(False)
        self._worker = KamiQueryWorker(kami_codes, parent=self)
        self._worker.query_done.connect(on_done)
        self._worker.query_done.connect(self._cleanupWorker)
        self._worker.error_occurred.connect(self._onWorkerError)
        self._worker.error_occurred.connect(self._cleanupWorker)
        self._worker.start()

    def _onWorkerError(self, msg: str) -> None:
        InfoBar.error(
            "API 错误", msg[:200],
            parent=self, position=InfoBarPosition.TOP, duration=5000,
        )

    def _cleanupWorker(self) -> None:
        self.btnImport.setEnabled(True)
        self.btnRefresh.setEnabled(True)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    # ── 删除 ──────────────────────────────────────────────

    def _deleteSelected(self) -> None:
        ids = self._getSelectedKamiIds()
        if not ids:
            InfoBar.warning(
                "提示", "请先选择要删除的卡密",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        dlg = MessageBox(
            "确认删除",
            f"确定要删除选中的 {len(ids)} 个卡密吗？此操作不可撤销。",
            self,
        )
        if not dlg.exec():
            return
        deleted = self._kami_db.delete_kamis(ids)
        InfoBar.info(
            "已删除", f"已删除 {deleted} 个卡密",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )

    # ── 右键菜单 ──────────────────────────────────────────

    def _showContextMenu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        kami_id = self._getKamiId(row)
        if kami_id is None:
            return
        menu = RoundMenu(parent=self.table)
        # 复制卡密
        code_item = self.table.item(row, 0)
        if code_item:
            menu.addAction(Action(
                FIF.COPY, "复制卡密",
                triggered=lambda: self._copyToClipboard(code_item.text()),
            ))
        menu.addSeparator()
        # 分配到节点
        status_item = self.table.item(row, _STATUS_COL)
        status_val = status_item.data(Qt.ItemDataRole.UserRole) if status_item else ""
        if status_val == "已激活":
            menu.addAction(Action(
                FIF.SEND, "分配到节点",
                triggered=lambda kid=kami_id: self._assignToNode(kid),
            ))
        # 解绑节点
        nodes_item = self.table.item(row, 2)
        if nodes_item and nodes_item.text() != "-":
            menu.addAction(Action(
                FIF.REMOVE, "解绑节点",
                triggered=lambda kid=kami_id: self._unbindFromNode(kid),
            ))
        menu.addSeparator()
        # 删除
        menu.addAction(Action(
            FIF.DELETE, "删除",
            triggered=lambda kid=kami_id: self._deleteSingle(kid),
        ))
        menu.exec(
            self.table.viewport().mapToGlobal(pos),
            aniType=MenuAnimationType.NONE,
        )

    def _assignToNode(self, kami_id: int) -> None:
        """弹出节点选择对话框"""
        nodes = self._getOnlineNodes()
        if not nodes:
            InfoBar.warning(
                "提示", "当前没有在线节点",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        dlg = MessageBox("选择节点", "选择要分配的节点:", self)
        combo = ComboBox()
        combo.addItems(nodes)
        dlg.textLayout.addWidget(combo)
        if not dlg.exec():
            return
        node_name = combo.currentText()
        if self._kami_db.bind_node(kami_id, node_name):
            InfoBar.success(
                "分配成功", f"已分配到 {node_name}",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
        else:
            InfoBar.warning(
                "分配失败", f"该卡密已绑定到 {node_name}",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )

    def _unbindFromNode(self, kami_id: int) -> None:
        """解绑：获取该卡密绑定的节点列表，让用户选择"""
        kamis = self._kami_db.get_all_kamis()
        target = next((k for k in kamis if k.id == kami_id), None)
        if not target or not target.bound_nodes:
            return
        if len(target.bound_nodes) == 1:
            self._kami_db.unbind_node(kami_id, target.bound_nodes[0])
            InfoBar.success(
                "已解绑", f"已解绑 {target.bound_nodes[0]}",
                parent=self, position=InfoBarPosition.TOP, duration=2000,
            )
            return
        dlg = MessageBox("选择要解绑的节点", "", self)
        combo = ComboBox()
        combo.addItems(target.bound_nodes)
        dlg.textLayout.addWidget(combo)
        if not dlg.exec():
            return
        node_name = combo.currentText()
        self._kami_db.unbind_node(kami_id, node_name)
        InfoBar.success(
            "已解绑", f"已解绑 {node_name}",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )

    def _deleteSingle(self, kami_id: int) -> None:
        dlg = MessageBox("确认删除", "确定要删除这个卡密吗？", self)
        if dlg.exec():
            self._kami_db.delete_kami(kami_id)

    # ── 辅助方法 ──────────────────────────────────────────

    def _getKamiId(self, row: int) -> int | None:
        item = self.table.item(row, 0)
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _getSelectedKamiIds(self) -> list[int]:
        ids = []
        for idx in self.table.selectionModel().selectedRows():
            kid = self._getKamiId(idx.row())
            if kid is not None:
                ids.append(kid)
        return ids

    def _getOnlineNodes(self) -> list[str]:
        """从 NodeManager 获取在线节点列表"""
        if not hasattr(self._node_manager, "nodes"):
            return []
        return [
            name for name, node in self._node_manager.nodes.items()
            if node.is_online()
        ]

    def _copyToClipboard(self, text: str) -> None:
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
        InfoBar.success(
            "已复制", "", parent=self,
            position=InfoBarPosition.TOP, duration=1500,
        )
