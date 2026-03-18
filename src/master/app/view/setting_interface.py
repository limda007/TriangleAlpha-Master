"""设置页面 — 网络/外观/关于"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBoxSettingCard,
    RangeSettingCard,
    ScrollArea,
    SettingCardGroup,
    SubtitleLabel,
    SwitchSettingCard,
    setTheme,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from master.app.common.config import cfg


class SettingInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingInterface")

        self.scrollWidget = QWidget(self)
        self.scrollWidget.setObjectName("view")
        self.mainLayout = QVBoxLayout(self.scrollWidget)
        self.mainLayout.setContentsMargins(24, 24, 24, 24)
        self.mainLayout.setSpacing(24)

        # ── 网络设置 ──
        self.networkGroup = SettingCardGroup("网络", self.scrollWidget)

        self.udpPortCard = RangeSettingCard(
            cfg.udpPort, FIF.WIFI, "UDP 监听端口",
            "接收被控端心跳和状态的端口", self.networkGroup,
        )
        self.tcpCmdPortCard = RangeSettingCard(
            cfg.tcpCmdPort, FIF.SEND, "TCP 指令端口",
            "向被控端发送指令的端口", self.networkGroup,
        )
        self.tcpLogPortCard = RangeSettingCard(
            cfg.tcpLogPort, FIF.DOCUMENT, "TCP 日志端口",
            "接收被控端日志的端口", self.networkGroup,
        )
        self.heartbeatCard = RangeSettingCard(
            cfg.heartbeatInterval, FIF.SYNC, "心跳间隔（秒）",
            "被控端发送心跳的间隔", self.networkGroup,
        )
        self.timeoutCard = RangeSettingCard(
            cfg.offlineTimeout, FIF.REMOVE, "离线超时（秒）",
            "超过此时间无心跳标记为离线", self.networkGroup,
        )

        self.networkGroup.addSettingCard(self.udpPortCard)
        self.networkGroup.addSettingCard(self.tcpCmdPortCard)
        self.networkGroup.addSettingCard(self.tcpLogPortCard)
        self.networkGroup.addSettingCard(self.heartbeatCard)
        self.networkGroup.addSettingCard(self.timeoutCard)
        self.mainLayout.addWidget(self.networkGroup)

        # ── 外观 ──
        self.uiGroup = SettingCardGroup("外观", self.scrollWidget)

        self.themeCard = ComboBoxSettingCard(
            cfg.themeMode, FIF.BRUSH, "应用主题", "切换深色/浅色外观",
            texts=["浅色", "深色", "跟随系统"],
            parent=self.uiGroup,
        )
        self.micaCard = SwitchSettingCard(
            FIF.TRANSPARENT, "Mica 效果", "启用半透明窗口效果 (仅 Windows 11)",
            cfg.micaEnabled, self.uiGroup,
        )

        self.uiGroup.addSettingCard(self.themeCard)
        self.uiGroup.addSettingCard(self.micaCard)
        self.mainLayout.addWidget(self.uiGroup)

        # ── 关于 ──
        self.aboutGroup = SettingCardGroup("关于", self.scrollWidget)
        aboutCard = QWidget(self.aboutGroup)
        aboutLayout = QVBoxLayout(aboutCard)
        aboutLayout.setContentsMargins(20, 16, 20, 16)
        aboutLayout.addWidget(SubtitleLabel("TriangleAlpha 群控中心"))
        aboutLayout.addWidget(BodyLabel("版本 0.1.0"))
        aboutLayout.addWidget(BodyLabel("基于 PyQt6 + PyQt-Fluent-Widgets 构建"))
        aboutLayout.addSpacing(8)
        aboutLayout.addWidget(BodyLabel("群控系统：管理 50+ 游戏被控端节点"))
        self.mainLayout.addWidget(aboutCard)

        self.mainLayout.addStretch()

        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ── 信号 ──
        cfg.themeChanged.connect(setTheme)
