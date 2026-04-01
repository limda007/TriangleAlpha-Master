"""本地 IPC 接收器测试。"""
from __future__ import annotations

import time

import pydantic
import pytest

from slave.ipc_receiver import LocalIpcReceiver, parse_ipc_status
from slave.models import IpcData


class TestParseIpcStatus:
    """测试 STATUS 消息解析。"""

    def test_valid_status_message(self):
        raw = b"STATUS|placeholder|account01|15|3500|\xe8\xbf\x90\xe8\xa1\x8c\xe4\xb8\xad|120"
        result = parse_ipc_status(raw)
        assert result is not None
        assert isinstance(result, IpcData)
        assert result.level == "15"
        assert result.jinbi == "3500"
        assert result.status_text == "运行中"
        assert result.elapsed == "120"

    def test_parts1_is_ignored(self):
        """parts[1] 应被忽略，不管内容是什么。"""
        raw = b"STATUS|anything_here|acc|10|500|ok|60"
        result = parse_ipc_status(raw)
        assert result is not None
        assert isinstance(result, IpcData)
        assert result.level == "10"

    def test_too_few_fields_returns_none(self):
        assert parse_ipc_status(b"STATUS|only|three") is None

    def test_non_status_prefix_returns_none(self):
        assert parse_ipc_status(b"HEARTBEAT|a|b|c|d|e|f") is None

    def test_empty_data_returns_none(self):
        assert parse_ipc_status(b"") is None

    def test_invalid_utf8_returns_none(self):
        assert parse_ipc_status(b"\xff\xfe\x00\x01") is None

    def test_extra_fields_are_tolerated(self):
        raw = b"STATUS|x|acc|10|500|ok|60|extra1|extra2"
        result = parse_ipc_status(raw)
        assert result is not None
        assert isinstance(result, IpcData)
        assert result.elapsed == "60"


class TestLocalIpcReceiver:
    """测试 IPC 缓存和 snapshot。"""

    def test_snapshot_returns_none_initially(self):
        receiver = LocalIpcReceiver()
        data, age = receiver.snapshot()
        assert data is None
        assert age == float("inf")

    def test_update_and_snapshot(self):
        receiver = LocalIpcReceiver()
        receiver._on_message(IpcData(
            level="10", jinbi="500", status_text="运行中", elapsed="30",
        ))
        data, age = receiver.snapshot()
        assert data is not None
        assert data.level == "10"
        assert age < 1.0

    def test_snapshot_age_increases(self):
        receiver = LocalIpcReceiver()
        receiver._last_sync = time.monotonic() - 10
        receiver._data = IpcData(level="1", jinbi="0", status_text="", elapsed="0")
        _, age = receiver.snapshot()
        assert age >= 9.5

    def test_snapshot_returns_frozen_model(self):
        """snapshot 返回 frozen Pydantic 模型，不可变。"""
        receiver = LocalIpcReceiver()
        receiver._on_message(IpcData(
            level="10", jinbi="500", status_text="ok", elapsed="30",
        ))
        data1, _ = receiver.snapshot()
        assert data1 is not None
        with pytest.raises(pydantic.ValidationError):
            data1.level = "modified"  # type: ignore[misc]
        data2, _ = receiver.snapshot()
        assert data2.level == "10"
