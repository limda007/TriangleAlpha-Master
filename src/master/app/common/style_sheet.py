"""QSS 样式表管理"""
from enum import Enum

from qfluentwidgets import StyleSheetBase, Theme, qconfig

from master.app.common.config import RESOURCE_DIR


class StyleSheet(StyleSheetBase, Enum):
    NODE_INTERFACE = "node_interface"
    DASHBOARD_INTERFACE = "dashboard_interface"
    ACCOUNT_INTERFACE = "account_interface"
    LOG_INTERFACE = "log_interface"

    def path(self, theme=Theme.AUTO):
        theme = qconfig.theme if theme == Theme.AUTO else theme
        return str(RESOURCE_DIR / "qss" / theme.value.lower() / f"{self.value}.qss")
