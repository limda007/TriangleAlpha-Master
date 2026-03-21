"""Slave 统一日志配置。"""
from __future__ import annotations

import logging
import sys
from collections.abc import Callable

_LOGGER_ROOT = "trianglealpha"


class _GuiSignalHandler(logging.Handler):
    def __init__(self, sink: Callable[[str], None]) -> None:
        super().__init__()
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._sink(self.format(record))
        except Exception:
            self.handleError(record)


def configure_slave_logging(gui_sink: Callable[[str], None] | None = None) -> logging.Logger:
    """重建 slave 的统一日志出口。"""
    logger = logging.getLogger(_LOGGER_ROOT)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if gui_sink is not None:
        gui_handler = _GuiSignalHandler(gui_sink)
        gui_handler.setFormatter(formatter)
        logger.addHandler(gui_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_ROOT}.{name}")
