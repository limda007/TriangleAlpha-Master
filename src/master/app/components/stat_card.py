"""统计卡片组件"""
from PyQt6.QtWidgets import QLabel, QVBoxLayout

from qfluentwidgets import SimpleCardWidget


class StatCard(SimpleCardWidget):
    def __init__(self, title: str, value: str = "0", parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setFixedHeight(110)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)

        self.titleLabel = QLabel(title, self)
        self.titleLabel.setObjectName("statTitle")
        layout.addWidget(self.titleLabel)

        layout.addStretch(1)

        self.valueLabel = QLabel(value, self)
        self.valueLabel.setObjectName("statValue")
        layout.addWidget(self.valueLabel)

    def setValue(self, value: str) -> None:
        self.valueLabel.setText(value)
