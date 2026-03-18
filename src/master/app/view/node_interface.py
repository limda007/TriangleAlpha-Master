"""节点管理页面（占位）"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from qfluentwidgets import ScrollArea

from master.app.core.account_pool import AccountPool
from master.app.core.node_manager import NodeManager
from master.app.core.tcp_commander import TcpCommander


class NodeInterface(ScrollArea):
    def __init__(self, node_mgr: NodeManager, tcp_cmd: TcpCommander,
                 account_pool: AccountPool, parent=None):
        super().__init__(parent)
        self.setObjectName("nodeInterface")
        self._nm = node_mgr
        self._tcp = tcp_cmd
        self._pool = account_pool

        self.view = QWidget(self)
        self.view.setObjectName("view")
        layout = QVBoxLayout(self.view)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(QLabel("节点管理 -- 待实现"))
        layout.addStretch()

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
