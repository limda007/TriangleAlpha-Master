"""中控端入口"""
import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from master.app.common.config import RESOURCE_DIR
from master.app.view.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    icon_path = RESOURCE_DIR / "icon_256.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
