"""兼容旧导入名 `common_app_version`。"""
from __future__ import annotations

from common.app_version import read_project_version

__all__ = ["read_project_version"]
