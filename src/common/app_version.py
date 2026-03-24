"""项目版本号读取。"""
from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

_VERSION_PATTERN = re.compile(r'^\s*version\s*=\s*["\']([^"\']+)["\']\s*$')
_DEFAULT_VERSION = "0.0.0"


def _candidate_pyproject_paths() -> list[Path]:
    candidates: list[Path] = []
    packaged_root = getattr(sys, "_MEIPASS", "")
    if packaged_root:
        candidates.append(Path(packaged_root) / "pyproject.toml")
    candidates.append(Path(__file__).resolve().parents[2] / "pyproject.toml")
    return candidates


def read_project_version(
    default: str = _DEFAULT_VERSION,
    *,
    candidate_paths: Iterable[Path] | None = None,
) -> str:
    """从 pyproject.toml 读取项目版本号。"""
    paths = list(candidate_paths) if candidate_paths is not None else _candidate_pyproject_paths()
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            match = _VERSION_PATTERN.match(line)
            if match:
                return match.group(1).strip() or default
    return default
