"""应用配置"""
from __future__ import annotations

import sys
from pathlib import Path

from qfluentwidgets import (
    BoolValidator,
    ConfigItem,
    EnumSerializer,
    OptionsConfigItem,
    OptionsValidator,
    QConfig,
    RangeConfigItem,
    RangeValidator,
    Theme,
    qconfig,
)


def _get_resource_dir() -> Path:
    """兼容 PyInstaller onefile 和源码两种模式"""
    # PyInstaller 解压目录
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "master" / "app" / "resource"
    return Path(__file__).parent.parent / "resource"


RESOURCE_DIR = _get_resource_dir()
CONFIG_DIR = Path.home() / ".triangle-alpha"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


class AppConfig(QConfig):
    udpPort = RangeConfigItem("Network", "UdpPort", 8888, RangeValidator(1024, 65535))
    tcpCmdPort = RangeConfigItem("Network", "TcpCmdPort", 9999, RangeValidator(1024, 65535))
    tcpLogPort = RangeConfigItem("Network", "TcpLogPort", 8890, RangeValidator(1024, 65535))
    heartbeatInterval = RangeConfigItem("Network", "HeartbeatInterval", 3, RangeValidator(1, 30))
    offlineTimeout = RangeConfigItem("Network", "OfflineTimeout", 15, RangeValidator(5, 120))
    themeMode = OptionsConfigItem(
        "UI", "ThemeMode", Theme.AUTO, OptionsValidator(Theme),
        EnumSerializer(Theme), restart=False,
    )
    micaEnabled = ConfigItem("UI", "MicaEnabled", False, BoolValidator())

    # 平台对接
    platformEnabled = ConfigItem("Platform", "Enabled", False, BoolValidator())
    platformApiUrl = ConfigItem("Platform", "ApiUrl", "https://gc.limda10086.eu.org", None)
    platformUsername = ConfigItem("Platform", "Username", "", None)
    platformPassword = ConfigItem("Platform", "Password", "", None)
    platformGroupName = ConfigItem("Platform", "GroupName", "", None)

    # ── 中控发现 (机房多客户租户隔离) ────────────────────────
    # tenantId 非空 → 仅响应 / 接受同租户的 agent / master.
    # strictTenant=True → 拒绝任意一端为空的混合配置, 防误绑.
    # masterName 用于 MASTER_HERE 应答标识, 默认空 → 由 udp_listener 退化到 platform.node().
    tenantId = ConfigItem("Discovery", "TenantId", "", None)
    strictTenant = ConfigItem("Discovery", "StrictTenant", True, BoolValidator())
    masterName = ConfigItem("Discovery", "MasterName", "", None)


cfg = AppConfig()
qconfig.load(str(CONFIG_DIR / "config.json"), cfg)
