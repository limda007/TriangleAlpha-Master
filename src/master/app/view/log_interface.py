"""实时日志页面 — 远程查看被控端 Console 输出"""
from __future__ import annotations

from collections import defaultdict

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    PushButton,
    ScrollArea,
    SearchLineEdit,
    SubtitleLabel,
    TogglePushButton,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from master.app.core.log_receiver import LogEntry, LogReceiverThread


class LogInterface(ScrollArea):
    """实时日志页面

    布局:
    ┌──────────────────────────────────────────────────┐
    │  工具栏: [搜索] [级别▼] [▶ 自动滚动] [清空]      │
    ├───────────┬──────────────────────────────────────┤
    │  节点列表  │  日志输出                             │
    │  ☑ VM-01  │  [12:30:01] [INFO] 底层服务就绪      │
    │  ☑ VM-02  │  [12:30:04] [INFO] 收到启动指令      │
    │  ☐ VM-03  │  [12:30:05] [WARN] TestDemo 未找到   │
    └───────────┴──────────────────────────────────────┘
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logInterface")
        self._logs: dict[str, list[str]] = defaultdict(list)  # machine -> [formatted_lines]
        self._max_lines = 5000  # 每个节点最多保留行数
        self._auto_scroll = True
        self._receiver: LogReceiverThread | None = None

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.mainLayout = QVBoxLayout(self.view)
        self.mainLayout.setContentsMargins(24, 24, 24, 24)
        self.mainLayout.setSpacing(12)

        # ── 工具栏 ──
        toolLayout = QHBoxLayout()
        toolLayout.setSpacing(8)

        self.searchBox = SearchLineEdit(self)
        self.searchBox.setPlaceholderText("过滤日志关键词...")
        self.searchBox.setFixedWidth(250)
        toolLayout.addWidget(self.searchBox)

        self.levelCombo = ComboBox(self)
        self.levelCombo.addItems(["全部级别", "INFO", "WARN", "ERROR"])
        self.levelCombo.setFixedWidth(110)
        toolLayout.addWidget(self.levelCombo)

        toolLayout.addStretch()

        self.btnAutoScroll = TogglePushButton("自动滚动", self)
        self.btnAutoScroll.setChecked(True)
        toolLayout.addWidget(self.btnAutoScroll)

        self.btnClear = PushButton(FIF.DELETE, "清空", self)
        toolLayout.addWidget(self.btnClear)

        self.mainLayout.addLayout(toolLayout)

        # ── 主区域: 左节点列表 + 右日志 ──
        contentLayout = QHBoxLayout()
        contentLayout.setSpacing(12)

        # 左: 节点列表
        leftWidget = QWidget(self)
        leftLayout = QVBoxLayout(leftWidget)
        leftLayout.setContentsMargins(0, 0, 0, 0)
        leftLayout.setSpacing(4)
        leftLayout.addWidget(SubtitleLabel("节点", leftWidget))

        self.nodeList = QListWidget(leftWidget)
        self.nodeList.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.nodeList.setFixedWidth(160)
        leftLayout.addWidget(self.nodeList)

        self.btnSelectAll = PushButton("全选", leftWidget)
        leftLayout.addWidget(self.btnSelectAll)
        contentLayout.addWidget(leftWidget)

        # 右: 日志输出
        self.logOutput = QPlainTextEdit(self)
        self.logOutput.setReadOnly(True)
        self.logOutput.setObjectName("logOutput")
        contentLayout.addWidget(self.logOutput, 1)
        self.mainLayout.addLayout(contentLayout, 1)

        # ── 空状态 ──
        self.emptyLabel = QWidget(self)
        emptyLayout = QVBoxLayout(self.emptyLabel)
        emptyLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emptyLayout.addWidget(SubtitleLabel("等待日志..."))
        emptyLayout.addWidget(BodyLabel("被控端启动后，日志会实时显示在这里"))
        self.mainLayout.addWidget(self.emptyLabel)
        self.emptyLabel.setVisible(True)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ── 信号 ──
        self.btnAutoScroll.toggled.connect(self._toggleAutoScroll)
        self.btnClear.clicked.connect(self._clearLogs)
        self.btnSelectAll.clicked.connect(self._selectAllNodes)
        self.searchBox.textChanged.connect(self._refreshDisplay)
        self.levelCombo.currentTextChanged.connect(self._refreshDisplay)
        self.nodeList.itemSelectionChanged.connect(self._refreshDisplay)

    def set_receiver(self, receiver: LogReceiverThread) -> None:
        """由 MainWindow 调用，连接日志接收线程"""
        self._receiver = receiver
        self._receiver.log_received.connect(self._onLogReceived)

    def _onLogReceived(self, entry: LogEntry) -> None:
        name = entry.machine_name
        formatted = f"[{entry.timestamp}] [{entry.level}] {entry.content}"

        # 添加到缓存
        lines = self._logs[name]
        lines.append(formatted)
        if len(lines) > self._max_lines:
            self._logs[name] = lines[-self._max_lines:]

        # 更新节点列表
        found = False
        for i in range(self.nodeList.count()):
            if self.nodeList.item(i).text() == name:
                found = True
                break
        if not found:
            item = QListWidgetItem(name)
            self.nodeList.addItem(item)
            item.setSelected(True)

        # 追加到显示
        selected_names = {
            self.nodeList.item(i).text()
            for i in range(self.nodeList.count())
            if self.nodeList.item(i).isSelected()
        }
        if name in selected_names and self._matchFilter(formatted, entry.level):
            self.logOutput.appendPlainText(f"[{name}] {formatted}")
            if self._auto_scroll:
                self.logOutput.verticalScrollBar().setValue(self.logOutput.verticalScrollBar().maximum())

        self.emptyLabel.setVisible(False)

    def _matchFilter(self, line: str, level: str) -> bool:
        search = self.searchBox.text().lower()
        level_filter = self.levelCombo.currentText()
        if search and search not in line.lower():
            return False
        return not (level_filter != "全部级别" and level != level_filter)

    def _refreshDisplay(self) -> None:
        selected_names = {
            self.nodeList.item(i).text()
            for i in range(self.nodeList.count())
            if self.nodeList.item(i).isSelected()
        }
        level_filter = self.levelCombo.currentText()
        search = self.searchBox.text().lower()

        filtered_lines: list[str] = []
        for name in sorted(selected_names):
            for line in self._logs.get(name, []):
                # 提取级别
                level = ""
                if "] [" in line:
                    parts = line.split("] [", 1)
                    if len(parts) > 1:
                        level = parts[1].split("]")[0] if "]" in parts[1] else ""
                if level_filter != "全部级别" and level != level_filter:
                    continue
                if search and search not in line.lower():
                    continue
                filtered_lines.append(f"[{name}] {line}")

        self.logOutput.setUpdatesEnabled(False)
        self.logOutput.setPlainText("\n".join(filtered_lines))
        self.logOutput.setUpdatesEnabled(True)

    def _toggleAutoScroll(self, checked: bool) -> None:
        self._auto_scroll = checked

    def _clearLogs(self) -> None:
        self._logs.clear()
        self.logOutput.clear()

    def _selectAllNodes(self) -> None:
        for i in range(self.nodeList.count()):
            self.nodeList.item(i).setSelected(True)
