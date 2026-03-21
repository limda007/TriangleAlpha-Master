"""Slave 运行路径解析。"""
from __future__ import annotations

import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    cwd = Path.cwd()
    if (cwd / "TestDemo.exe").exists() or (cwd / "主控IP.txt").exists() or (cwd / "master.txt").exists():
        return cwd
    return Path(__file__).parent


def get_resource_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "slave" / "resource"
    return Path(__file__).parent / "resource"


RESOURCE_DIR = get_resource_dir()
