"""平台 API 客户端 — 纯 Python，不依赖 Qt"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


class PlatformAPIError(Exception):
    """平台 API 调用异常"""


@dataclass
class PlatformClient:
    """群控账号分销平台 HTTP 客户端

    - 纯 Python 类，不依赖 Qt（便于单测）
    - 不持有 httpx.Client 实例 — 每次调用由外部传入或内部短生命周期创建
    - Token 管理: access_token + refresh_token 存在内存中
    - 401 自动重试: refresh → re-login → 放弃
    """

    base_url: str = ""
    username: str = ""
    password: str = ""
    access_token: str = ""
    refresh_token: str = ""
    _timeout: float = field(default=15.0, repr=False)

    def login(self, client: httpx.Client) -> None:
        """登录获取 token 对"""
        if not self.base_url or not self.username or not self.password:
            raise PlatformAPIError("登录失败：缺少 API 地址/用户名/密码")
        try:
            resp = client.post(
                f"{self.base_url}/api/v1/login",
                json={"username": self.username, "password": self.password},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise PlatformAPIError(f"登录失败：{e}") from e
        data = resp.json()
        self.access_token = data.get("access_token", "")
        self.refresh_token = data.get("refresh_token", "")
        if not self.access_token:
            raise PlatformAPIError("登录失败：响应中无 access_token")

    def refresh(self, client: httpx.Client) -> None:
        """使用 refresh_token 刷新 access_token"""
        if not self.refresh_token:
            raise PlatformAPIError("刷新令牌失败：无 refresh_token")
        try:
            resp = client.post(
                f"{self.base_url}/api/v1/refresh",
                json={"refresh_token": self.refresh_token},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise PlatformAPIError(f"刷新令牌失败：{e}") from e
        data = resp.json()
        self.access_token = data.get("access_token", "")
        if rt := data.get("refresh_token"):
            self.refresh_token = rt
        if not self.access_token:
            raise PlatformAPIError("刷新令牌失败：响应中无 access_token")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _ensure_auth(self, client: httpx.Client) -> None:
        """确保有有效 token，无则登录"""
        if not self.access_token:
            self.login(client)

    def _retry_on_401(
        self, client: httpx.Client, method: str, url: str, **kwargs: object,
    ) -> httpx.Response:
        """带 401 自动重试的请求：refresh → re-login → 放弃"""
        self._ensure_auth(client)
        kwargs.setdefault("timeout", self._timeout)  # type: ignore[arg-type]
        kwargs["headers"] = self._auth_headers()
        try:
            resp = client.request(method, url, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as e:
            raise PlatformAPIError(f"请求失败：{e}") from e

        if resp.status_code != 401:
            return resp

        # 401 → 尝试刷新
        try:
            self.refresh(client)
        except PlatformAPIError:
            # refresh 失败 → 尝试重新登录
            try:
                self.login(client)
            except PlatformAPIError as e:
                raise PlatformAPIError(f"登录失败：{e}") from e

        # 重试一次
        kwargs["headers"] = self._auth_headers()
        try:
            resp = client.request(method, url, **kwargs)  # type: ignore[arg-type]
            return resp
        except httpx.HTTPError as e:
            raise PlatformAPIError(f"请求失败：{e}") from e

    def import_accounts(
        self, client: httpx.Client, text: str, group_name: str,
    ) -> dict:
        """上传账号到平台（multipart/form-data）"""
        resp = self._retry_on_401(
            client, "POST",
            f"{self.base_url}/api/v1/producer/import",
            files={
                "text": (None, text),
                "group_name": (None, group_name),
            },
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise PlatformAPIError(f"上传失败({resp.status_code})：{resp.text}") from e
        return resp.json()

    def query_accounts(
        self, client: httpx.Client, group_name: str,
    ) -> list[dict]:
        """查询平台已取号的账号列表"""
        resp = self._retry_on_401(
            client, "GET",
            f"{self.base_url}/api/v1/producer/accounts",
            params={"group_name": group_name, "status": "taken"},
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise PlatformAPIError(f"查询失败({resp.status_code})：{resp.text}") from e
        data = resp.json()
        # 兼容 {items: [...]} 和 [...] 两种格式
        if isinstance(data, list):
            return data
        return data.get("items", [])
