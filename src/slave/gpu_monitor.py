"""GPU 显存监控：通过 nvidia-smi 获取 VRAM 使用情况。"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time

from slave.logging_utils import get_logger

logger = get_logger(__name__)

_CACHE_TTL = 10  # 缓存有效期（秒）
_cache_ts: float = 0.0
_cache_val: tuple[int, int] = (0, 0)


async def get_vram_info() -> tuple[int, int]:
    """返回 (used_mb, total_mb)，不可用时返回 (0, 0)。"""
    global _cache_ts, _cache_val
    now = time.monotonic()
    if now - _cache_ts < _CACHE_TTL:
        return _cache_val

    used, total = 0, 0
    try:
        # Windows 上必须 CREATE_NO_WINDOW 防止 conhost 弹窗闪烁
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi", "--query-gpu=memory.used,memory.total",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **kwargs,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0 and stdout:
            line = stdout.decode("utf-8").strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                used, total = int(parts[0]), int(parts[1])
    except FileNotFoundError:
        pass
    except TimeoutError:
        logger.debug("nvidia-smi 超时")
    except (ValueError, IndexError, OSError) as exc:
        logger.debug("nvidia-smi 解析失败: %s", exc)

    _cache_ts = now
    _cache_val = (used, total)
    return used, total
