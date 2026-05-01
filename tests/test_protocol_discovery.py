"""TDD: Master 端 DISCOVER_MASTER 解析 + 租户匹配 + MASTER_HERE 构造.

Master 仓侧只测纯函数 (无 Qt / 无 socket), Qt 线程逻辑由集成测试覆盖.
"""
from __future__ import annotations

import pytest

from common.protocol import (
    build_udp_master_here,
    parse_discover_master,
    should_reply_to_discovery,
)


# ── parse_discover_master ─────────────────────────────────────


def test_parse_legacy_4_field() -> None:
    parsed = parse_discover_master("DISCOVER_MASTER|agent-1|0.3.0|1")
    assert parsed is not None
    assert parsed == ("agent-1", "0.3.0", "1", "")


def test_parse_new_5_field_with_tenant() -> None:
    parsed = parse_discover_master("DISCOVER_MASTER|agent-1|0.3.0|1|client-X")
    assert parsed == ("agent-1", "0.3.0", "1", "client-X")


def test_parse_rejects_wrong_prefix() -> None:
    assert parse_discover_master("OTHER|a|b|c") is None


def test_parse_rejects_too_many_fields() -> None:
    assert parse_discover_master("DISCOVER_MASTER|a|b|c|t|extra") is None


def test_parse_rejects_too_few_fields() -> None:
    assert parse_discover_master("DISCOVER_MASTER|a|b") is None


# ── should_reply_to_discovery (租户策略对称版) ────────────


@pytest.mark.parametrize(
    ("local", "remote", "strict", "expected"),
    [
        ("X", "X", True, True),
        ("X", "Y", True, False),
        ("X", "", True, False),
        ("X", "", False, True),
        ("", "", True, True),
        ("", "Y", True, False),
        ("", "Y", False, True),
        ("X", "Y", False, False),
    ],
)
def test_should_reply_matrix(local: str, remote: str, strict: bool, expected: bool) -> None:
    assert should_reply_to_discovery(remote, local_tenant=local, strict=strict) is expected


# ── build_udp_master_here (向后兼容) ─────────────────────


def test_build_legacy_when_tenant_empty() -> None:
    s = build_udp_master_here("master-A", 9999, 8890, "1", tenant_id="")
    assert s == "MASTER_HERE|master-A|9999|8890|1"


def test_build_includes_tenant_when_set() -> None:
    s = build_udp_master_here("master-A", 9999, 8890, "1", tenant_id="client-X")
    assert s == "MASTER_HERE|master-A|9999|8890|1|client-X"


def test_build_rejects_pipe_in_master_name() -> None:
    with pytest.raises(ValueError):
        build_udp_master_here("a|b", 9999, 8890, "1", tenant_id="")


def test_build_rejects_pipe_in_tenant() -> None:
    with pytest.raises(ValueError):
        build_udp_master_here("master-A", 9999, 8890, "1", tenant_id="a|b")
