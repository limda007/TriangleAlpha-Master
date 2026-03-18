"""仪表盘页面"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from qfluentwidgets import ScrollArea

from master.app.core.account_pool import AccountPool
from master.app.core.node_manager import NodeManager


class DashboardInterface(ScrollArea):
    def __init__(self, node_manager: NodeManager, account_pool: AccountPool, parent=None):
        super().__init__(parent)
        self.setObjectName("dashboardInterface")
        self._nm = node_manager
        self._pool = account_pool

        self.view = QWidget(self)
        self.view.setObjectName("view")
        layout = QVBoxLayout(self.view)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(QLabel("仪表盘 -- 待实现"))
        layout.addStretch()

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
