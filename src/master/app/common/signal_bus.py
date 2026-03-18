"""全局信号总线"""
from PyQt6.QtCore import QObject, pyqtSignal


class SignalBus(QObject):
    start_nodes = pyqtSignal(list)
    stop_nodes = pyqtSignal(list)
    reboot_nodes = pyqtSignal(list)
    reboot_pc_nodes = pyqtSignal(list)
    distribute_keys = pyqtSignal()
    send_file = pyqtSignal(str, list)
    micaEnableChanged = pyqtSignal(bool)
    switch_to_node = pyqtSignal(str)


signalBus = SignalBus()
