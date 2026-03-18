"""操作历史页面"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from qfluentwidgets import ScrollArea

from master.app.core.node_manager import NodeManager


class HistoryInterface(ScrollArea):
    def __init__(self, node_manager: NodeManager, parent=None):
        super().__init__(parent)
        self.setObjectName("historyInterface")
        self._nm = node_manager

        self.view = QWidget(self)
        self.view.setObjectName("view")
        layout = QVBoxLayout(self.view)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(QLabel("操作历史 -- 待实现"))
        layout.addStretch()

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
