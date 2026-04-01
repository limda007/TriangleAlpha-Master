"""GPU 显存监控测试。"""
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from slave.gpu_monitor import get_vram_info


class TestGetVramInfo:
    def _call_fresh(self, **kwargs):
        """调用 get_vram_info 并强制跳过缓存。"""
        mock_proc = kwargs.get("mock_proc")
        side_effect = kwargs.get("side_effect")

        with patch("slave.gpu_monitor._cache_ts", 0.0), \
             patch("slave.gpu_monitor._cache_val", (0, 0)):
            if side_effect is not None:
                with patch("slave.gpu_monitor.asyncio.create_subprocess_exec",
                           side_effect=side_effect):
                    return asyncio.run(get_vram_info())
            else:
                with patch("slave.gpu_monitor.asyncio.create_subprocess_exec",
                           return_value=mock_proc):
                    return asyncio.run(get_vram_info())

    @staticmethod
    def _make_proc(returncode: int, stdout_data: str):
        proc = AsyncMock()
        proc.communicate.return_value = (stdout_data.encode("utf-8"), b"")
        proc.returncode = returncode
        return proc

    def test_nvidia_smi_success(self):
        proc = self._make_proc(0, "4200, 6144\n")
        used, total = self._call_fresh(mock_proc=proc)
        assert used == 4200
        assert total == 6144

    def test_nvidia_smi_not_found(self):
        used, total = self._call_fresh(side_effect=FileNotFoundError)
        assert used == 0
        assert total == 0

    def test_nvidia_smi_timeout(self):
        async def _timeout_exec(*args, **kw):
            proc = AsyncMock()
            proc.communicate.side_effect = asyncio.TimeoutError
            return proc

        with patch("slave.gpu_monitor._cache_ts", 0.0), \
             patch("slave.gpu_monitor._cache_val", (0, 0)), \
             patch("slave.gpu_monitor.asyncio.create_subprocess_exec",
                   side_effect=_timeout_exec), \
             patch("slave.gpu_monitor.asyncio.wait_for",
                   side_effect=asyncio.TimeoutError):
            used, total = asyncio.run(get_vram_info())
        assert used == 0
        assert total == 0

    def test_multiple_gpus_takes_first(self):
        proc = self._make_proc(0, "4200, 6144\n2048, 8192\n")
        used, total = self._call_fresh(mock_proc=proc)
        assert used == 4200
        assert total == 6144

    def test_bad_output_returns_zero(self):
        proc = self._make_proc(0, "not_a_number, bad\n")
        used, total = self._call_fresh(mock_proc=proc)
        assert used == 0
        assert total == 0

    def test_nonzero_return_code(self):
        proc = self._make_proc(1, "")
        used, total = self._call_fresh(mock_proc=proc)
        assert used == 0
        assert total == 0

    def test_caching_skips_subprocess(self):
        import time
        with patch("slave.gpu_monitor._cache_ts", time.monotonic()), \
             patch("slave.gpu_monitor._cache_val", (999, 888)), \
             patch("slave.gpu_monitor.asyncio.create_subprocess_exec") as mock_exec:
            used, total = asyncio.run(get_vram_info())
        mock_exec.assert_not_called()
        assert used == 999
        assert total == 888
