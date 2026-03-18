"""应用配置"""
from __future__ import annotations

from pathlib import Path

from qfluentwidgets import (
    BoolValidator,
    ConfigItem,
    QConfig,
    RangeConfigItem,
    RangeValidator,
    qconfig,
)

RESOURCE_DIR = Path(__file__).parent.parent / "resource"
CONFIG_DIR = Path.home() / ".triangle-alpha"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


class AppConfig(QConfig):
    udpPort = RangeConfigItem("Network", "UdpPort", 8888, RangeValidator(1024, 65535))
    tcpCmdPort = RangeConfigItem("Network", "TcpCmdPort", 9999, RangeValidator(1024, 65535))
    tcpLogPort = RangeConfigItem("Network", "TcpLogPort", 8890, RangeValidator(1024, 65535))
    heartbeatInterval = RangeConfigItem("Network", "HeartbeatInterval", 3, RangeValidator(1, 30))
    offlineTimeout = RangeConfigItem("Network", "OfflineTimeout", 15, RangeValidator(5, 120))
    micaEnabled = ConfigItem("UI", "MicaEnabled", False, BoolValidator())


cfg = AppConfig()
qconfig.load(str(CONFIG_DIR / "config.json"), cfg)
