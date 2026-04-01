"""GPU 显存监控测试。"""
import subprocess
from unittest.mock import patch, MagicMock

from slave.gpu_monitor import get_vram_info


class TestGetVramInfo:
    def _call_fresh(self, **run_kwargs):
        """调用 get_vram_info 并强制跳过缓存。"""
        with patch("slave.gpu_monitor._cache", {"ts": 0.0, "val": (0, 0)}):
            if "side_effect" in run_kwargs:
                with patch("slave.gpu_monitor.subprocess.run", side_effect=run_kwargs["side_effect"]):
                    return get_vram_info()
            else:
                with patch("slave.gpu_monitor.subprocess.run", return_value=run_kwargs["return_value"]):
                    return get_vram_info()

    def test_nvidia_smi_success(self):
        mock_result = MagicMock(returncode=0, stdout="4200, 6144\n")
        used, total = self._call_fresh(return_value=mock_result)
        assert used == 4200
        assert total == 6144

    def test_nvidia_smi_not_found(self):
        used, total = self._call_fresh(side_effect=FileNotFoundError)
        assert used == 0
        assert total == 0

    def test_nvidia_smi_timeout(self):
        used, total = self._call_fresh(side_effect=subprocess.TimeoutExpired("cmd", 5))
        assert used == 0
        assert total == 0

    def test_multiple_gpus_takes_first(self):
        mock_result = MagicMock(returncode=0, stdout="4200, 6144\n2048, 8192\n")
        used, total = self._call_fresh(return_value=mock_result)
        assert used == 4200
        assert total == 6144

    def test_bad_output_returns_zero(self):
        mock_result = MagicMock(returncode=0, stdout="not_a_number, bad\n")
        used, total = self._call_fresh(return_value=mock_result)
        assert used == 0
        assert total == 0

    def test_nonzero_return_code(self):
        mock_result = MagicMock(returncode=1, stdout="")
        used, total = self._call_fresh(return_value=mock_result)
        assert used == 0
        assert total == 0

    def test_caching_skips_subprocess(self):
        import time
        mock_result = MagicMock(returncode=0, stdout="2048, 4096\n")
        with patch("slave.gpu_monitor.subprocess.run", return_value=mock_result) as mock_run:
            with patch("slave.gpu_monitor._cache", {"ts": time.monotonic(), "val": (999, 888)}):
                used, total = get_vram_info()
        mock_run.assert_not_called()
        assert used == 999
        assert total == 888
