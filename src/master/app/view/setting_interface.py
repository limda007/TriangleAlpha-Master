"""设置页面 — 网络/外观/平台对接/关于"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBoxSettingCard,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushSettingCard,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
    SpinBox,
    SwitchSettingCard,
    setTheme,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from master.app.common.config import cfg


class _LineEditSettingCard(SettingCard):
    """内嵌 LineEdit 的设置卡片"""

    def __init__(self, icon, title, content, config_item, parent=None):
        super().__init__(icon, title, content, parent)
        self._config_item = config_item
        self._edit = LineEdit(self)
        self._edit.setMinimumWidth(200)
        self._edit.setText(cfg.get(config_item))
        self._edit.editingFinished.connect(self._on_changed)
        self.hBoxLayout.addWidget(self._edit, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def _on_changed(self) -> None:
        cfg.set(self._config_item, self._edit.text())


class _PasswordSettingCard(SettingCard):
    """内嵌 PasswordLineEdit 的设置卡片"""

    def __init__(self, icon, title, content, config_item, parent=None):
        super().__init__(icon, title, content, parent)
        self._config_item = config_item
        self._edit = PasswordLineEdit(self)
        self._edit.setMinimumWidth(200)
        self._edit.setText(cfg.get(config_item))
        self._edit.editingFinished.connect(self._on_changed)
        self.hBoxLayout.addWidget(self._edit, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def _on_changed(self) -> None:
        cfg.set(self._config_item, self._edit.text())


class _SpinBoxSettingCard(SettingCard):
    """内嵌 SpinBox 的设置卡片"""

    def __init__(self, icon, title, content, config_item, parent=None):
        super().__init__(icon, title, content, parent)
        self._config_item = config_item
        self._spin = SpinBox(self)
        self._spin.setMinimumWidth(140)
        validator = config_item.validator
        if hasattr(validator, "min"):
            self._spin.setMinimum(validator.min)
        if hasattr(validator, "max"):
            self._spin.setMaximum(validator.max)
        self._spin.setValue(cfg.get(config_item))
        self._spin.valueChanged.connect(self._on_changed)
        self.hBoxLayout.addWidget(self._spin, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def _on_changed(self, value: int) -> None:
        cfg.set(self._config_item, value)


class SettingInterface(ScrollArea):
    platformSettingsChanged = pyqtSignal()

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

        self.udpPortCard = _SpinBoxSettingCard(
            FIF.WIFI, "UDP 监听端口",
            "接收被控端心跳和状态的端口", cfg.udpPort, self.networkGroup,
        )
        self.tcpCmdPortCard = _SpinBoxSettingCard(
            FIF.SEND, "TCP 指令端口",
            "向被控端发送指令的端口", cfg.tcpCmdPort, self.networkGroup,
        )
        self.tcpLogPortCard = _SpinBoxSettingCard(
            FIF.DOCUMENT, "TCP 日志端口",
            "接收被控端日志的端口", cfg.tcpLogPort, self.networkGroup,
        )
        self.heartbeatCard = _SpinBoxSettingCard(
            FIF.SYNC, "心跳间隔（秒）",
            "被控端发送心跳的间隔", cfg.heartbeatInterval, self.networkGroup,
        )
        self.timeoutCard = _SpinBoxSettingCard(
            FIF.REMOVE, "离线超时（秒）",
            "超过此时间无心跳标记为离线", cfg.offlineTimeout, self.networkGroup,
        )

        self.networkGroup.addSettingCard(self.udpPortCard)
        self.networkGroup.addSettingCard(self.tcpCmdPortCard)
        self.networkGroup.addSettingCard(self.tcpLogPortCard)
        self.networkGroup.addSettingCard(self.heartbeatCard)
        self.networkGroup.addSettingCard(self.timeoutCard)
        self.mainLayout.addWidget(self.networkGroup)

        portNotice = BodyLabel("  * 修改端口后需重启应用生效", self.scrollWidget)
        portNotice.setObjectName("portNotice")
        self.mainLayout.addWidget(portNotice)

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

        # ── 分销平台 ──
        self.platformGroup = SettingCardGroup("分销平台", self.scrollWidget)

        self.platformEnabledCard = SwitchSettingCard(
            FIF.GLOBE, "启用平台对接", "自动上传已完成账号到分销平台",
            cfg.platformEnabled, self.platformGroup,
        )
        self.platformApiUrlCard = _LineEditSettingCard(
            FIF.LINK, "API 地址", "平台 API 根地址（如 https://api.example.com）",
            cfg.platformApiUrl, self.platformGroup,
        )
        self.platformUsernameCard = _LineEditSettingCard(
            FIF.PEOPLE, "用户名", "平台登录用户名",
            cfg.platformUsername, self.platformGroup,
        )
        self.platformPasswordCard = _PasswordSettingCard(
            FIF.FINGERPRINT, "密码", "平台登录密码",
            cfg.platformPassword, self.platformGroup,
        )
        self.platformGroupNameCard = _LineEditSettingCard(
            FIF.FOLDER, "分组名称", "上传账号所属分组",
            cfg.platformGroupName, self.platformGroup,
        )

        self.platformGroup.addSettingCard(self.platformEnabledCard)
        self.platformGroup.addSettingCard(self.platformApiUrlCard)
        self.platformGroup.addSettingCard(self.platformUsernameCard)
        self.platformGroup.addSettingCard(self.platformPasswordCard)
        self.platformGroup.addSettingCard(self.platformGroupNameCard)
        self.mainLayout.addWidget(self.platformGroup)

        # ── 关于 ──
        self.aboutGroup = SettingCardGroup("关于", self.scrollWidget)
        self.aboutCard = PrimaryPushSettingCard(
            "检查更新",
            FIF.INFO,
            "TriangleAlpha 群控中心",
            "© 2026  版本 1.0.15",
            self.aboutGroup,
        )
        self.aboutGroup.addSettingCard(self.aboutCard)
        self.mainLayout.addWidget(self.aboutGroup)

        self.mainLayout.addStretch()

        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ── 信号 ──
        cfg.themeChanged.connect(setTheme)

        # 平台设置变更 → 通知 MainWindow
        cfg.platformEnabled.valueChanged.connect(lambda _: self.platformSettingsChanged.emit())
        self.platformApiUrlCard._edit.editingFinished.connect(self.platformSettingsChanged.emit)
        self.platformUsernameCard._edit.editingFinished.connect(self.platformSettingsChanged.emit)
        self.platformPasswordCard._edit.editingFinished.connect(self.platformSettingsChanged.emit)
        self.platformGroupNameCard._edit.editingFinished.connect(self.platformSettingsChanged.emit)
