"""GPU 显存监控：通过 nvidia-smi 获取 VRAM 使用情况。"""
from __future__ import annotations

import subprocess
import time

from slave.logging_utils import get_logger

logger = get_logger(__name__)

_CACHE_TTL = 10  # 缓存有效期（秒）
_cache: dict[str, float | tuple[int, int]] = {"ts": 0.0, "val": (0, 0)}


def get_vram_info() -> tuple[int, int]:
    """返回 (used_mb, total_mb)，不可用时返回 (0, 0)。"""
    now = time.monotonic()
    if now - _cache["ts"] < _CACHE_TTL:  # type: ignore[operator]
        return _cache["val"]  # type: ignore[return-value]

    used, total = 0, 0
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            line = result.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                used, total = int(parts[0]), int(parts[1])
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        logger.debug("nvidia-smi 超时")
    except (ValueError, IndexError) as exc:
        logger.debug("nvidia-smi 解析失败: %s", exc)

    _cache["ts"] = now
    _cache["val"] = (used, total)
    return used, total
