"""PlatformClient 单元测试 — httpx.MockTransport"""
from __future__ import annotations

import json

import httpx
import pytest

from master.app.core.platform_client import PlatformAPIError, PlatformClient


def _make_transport(handler):
    """创建 MockTransport"""
    return httpx.MockTransport(handler)


class TestLogin:
    def test_login_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v1/login"
            body = json.loads(request.content)
            assert body["username"] == "user"
            assert body["password"] == "pass"
            return httpx.Response(200, json={
                "access_token": "at_123",
                "refresh_token": "rt_456",
            })

        pc = PlatformClient(base_url="http://test", username="user", password="pass")
        with httpx.Client(transport=_make_transport(handler)) as client:
            pc.login(client)
        assert pc.access_token == "at_123"
        assert pc.refresh_token == "rt_456"

    def test_login_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid"})

        pc = PlatformClient(base_url="http://test", username="user", password="bad")
        with (
            httpx.Client(transport=_make_transport(handler)) as client,
            pytest.raises(PlatformAPIError, match="登录失败"),
        ):
            pc.login(client)

    def test_login_missing_config(self) -> None:
        pc = PlatformClient()
        with (
            httpx.Client(transport=_make_transport(lambda r: httpx.Response(200))) as client,
            pytest.raises(PlatformAPIError, match="缺少"),
        ):
            pc.login(client)


class TestRefresh:
    def test_refresh_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["refresh_token"] == "rt_old"
            return httpx.Response(200, json={
                "access_token": "at_new",
                "refresh_token": "rt_new",
            })

        pc = PlatformClient(base_url="http://test", refresh_token="rt_old")
        with httpx.Client(transport=_make_transport(handler)) as client:
            pc.refresh(client)
        assert pc.access_token == "at_new"
        assert pc.refresh_token == "rt_new"

    def test_refresh_no_token(self) -> None:
        pc = PlatformClient(base_url="http://test")
        with (
            httpx.Client(transport=_make_transport(lambda r: httpx.Response(200))) as client,
            pytest.raises(PlatformAPIError, match="无 refresh_token"),
        ):
            pc.refresh(client)


class TestImportAccounts:
    def test_import_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/producer/import":
                assert "Bearer at_ok" in request.headers.get("authorization", "")
                return httpx.Response(200, json={"imported": 2})
            return httpx.Response(404)

        pc = PlatformClient(base_url="http://test", access_token="at_ok")
        with httpx.Client(transport=_make_transport(handler)) as client:
            result = pc.import_accounts(client, "u1----p1\nu2----p2", "group1")
        assert result["imported"] == 2

    def test_import_401_triggers_refresh_retry(self) -> None:
        """401 → refresh → 重试成功"""
        call_count = {"import": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/producer/import":
                call_count["import"] += 1
                if call_count["import"] == 1:
                    return httpx.Response(401, json={"error": "expired"})
                return httpx.Response(200, json={"imported": 1})
            if request.url.path == "/api/v1/refresh":
                return httpx.Response(200, json={
                    "access_token": "at_refreshed",
                })
            return httpx.Response(404)

        pc = PlatformClient(
            base_url="http://test",
            access_token="at_old",
            refresh_token="rt_ok",
        )
        with httpx.Client(transport=_make_transport(handler)) as client:
            result = pc.import_accounts(client, "u1----p1", "group1")
        assert result["imported"] == 1
        assert pc.access_token == "at_refreshed"

    def test_import_401_refresh_fail_relogin(self) -> None:
        """401 → refresh失败 → re-login → 重试成功"""
        call_count = {"import": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/producer/import":
                call_count["import"] += 1
                if call_count["import"] == 1:
                    return httpx.Response(401, json={"error": "expired"})
                return httpx.Response(200, json={"imported": 1})
            if request.url.path == "/api/v1/refresh":
                return httpx.Response(401, json={"error": "invalid_rt"})
            if request.url.path == "/api/v1/login":
                return httpx.Response(200, json={
                    "access_token": "at_relogin",
                    "refresh_token": "rt_relogin",
                })
            return httpx.Response(404)

        pc = PlatformClient(
            base_url="http://test",
            username="user", password="pass",
            access_token="at_expired",
            refresh_token="rt_expired",
        )
        with httpx.Client(transport=_make_transport(handler)) as client:
            result = pc.import_accounts(client, "u1----p1", "group1")
        assert result["imported"] == 1
        assert pc.access_token == "at_relogin"


class TestQueryAccounts:
    def test_query_list_format(self) -> None:
        """直接返回 list"""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/producer/accounts":
                return httpx.Response(200, json=[
                    {"username": "u1", "status": "taken"},
                ])
            return httpx.Response(404)

        pc = PlatformClient(base_url="http://test", access_token="at_ok")
        with httpx.Client(transport=_make_transport(handler)) as client:
            result = pc.query_accounts(client, "group1")
        assert len(result) == 1
        assert result[0]["username"] == "u1"

    def test_query_items_wrapper(self) -> None:
        """返回 {items: [...]} 格式"""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/producer/accounts":
                return httpx.Response(200, json={
                    "items": [{"username": "u1"}, {"username": "u2"}],
                    "total": 2,
                })
            return httpx.Response(404)

        pc = PlatformClient(base_url="http://test", access_token="at_ok")
        with httpx.Client(transport=_make_transport(handler)) as client:
            result = pc.query_accounts(client, "group1")
        assert len(result) == 2

    def test_query_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        pc = PlatformClient(base_url="http://test", access_token="at_ok")
        with httpx.Client(transport=_make_transport(handler)) as client:
            result = pc.query_accounts(client, "group1")
        assert result == []
