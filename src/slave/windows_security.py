"""Windows 文件安全标记辅助。"""
from __future__ import annotations

import os
from pathlib import Path

from slave.logging_utils import get_logger

logger = get_logger(__name__)

_ZONE_IDENTIFIER_STREAM = "Zone.Identifier"


def zone_identifier_path(path: Path) -> str:
    """返回给定文件的 Zone.Identifier 流路径。"""
    return f"{path}:{_ZONE_IDENTIFIER_STREAM}"


def clear_zone_identifier(path: Path) -> bool:
    """尝试移除 Windows 下载标记，成功返回 True。"""
    if os.name != "nt":
        return False

    try:
        os.remove(zone_identifier_path(path))
    except FileNotFoundError:
        return False
    except OSError:
        logger.exception("清理文件安全标记失败: %s", path)
        return False

    logger.info("已清理文件安全标记: %s", path)
    return True
