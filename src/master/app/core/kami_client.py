"""authserver 卡密查询客户端 + QThread Worker"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from PyQt6.QtCore import QThread, pyqtSignal

_API_KEY = "darkyvm-api-key"


class KamiAPIError(Exception):
    """卡密 API 调用异常"""


@dataclass
class KamiApiClient:
    """authserver 卡密查询客户端 — 纯 Python，不依赖 Qt"""

    base_url: str = "https://vm.limda789.eu.org"
    api_key: str = _API_KEY
    _timeout: float = 15.0
    _batch_size: int = 100

    def query_batch(
        self, client: httpx.Client, kami_codes: list[str],
    ) -> list[dict]:
        """批量查询卡密状态，自动分批"""
        all_results: list[dict] = []
        for i in range(0, len(kami_codes), self._batch_size):
            chunk = kami_codes[i : i + self._batch_size]
            resp = client.post(
                f"{self.base_url}/v2/query-batch",
                json={"kamis": chunk},
                headers={"X-API-Key": self.api_key},
                timeout=self._timeout,
            )
            if resp.status_code != 200:
                raise KamiAPIError(
                    f"API 返回 {resp.status_code}: {resp.text[:200]}"
                )
            data = resp.json()
            results = data.get("results", [])
            all_results.extend(results)
        return all_results


class KamiQueryWorker(QThread):
    """后台线程：执行卡密批量查询，仅做 HTTP，不操作 DB"""

    query_done = pyqtSignal(list)       # API results 列表
    error_occurred = pyqtSignal(str)    # 错误消息

    def __init__(self, kami_codes: list[str], parent=None) -> None:
        super().__init__(parent)
        self._kami_codes = kami_codes

    def run(self) -> None:
        try:
            api_client = KamiApiClient()
            with httpx.Client() as http:
                results = api_client.query_batch(http, self._kami_codes)
            self.query_done.emit(results)
        except (KamiAPIError, httpx.HTTPError, Exception) as e:
            self.error_occurred.emit(str(e))
