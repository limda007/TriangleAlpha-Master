"""协议契约测试。"""
from __future__ import annotations

import base64
import json

from common.protocol import (
    TcpCommand,
    UdpMessageType,
    build_self_update_payload,
    build_tcp_command,
    build_udp_account_sync,
    build_udp_ext_online,
    parse_tcp_command,
    parse_udp_message,
)


class TestTcpProtocolContracts:
    def test_parse_unknown_command_returns_none(self):
        assert parse_tcp_command("NOT_A_COMMAND|payload") is None

    def test_parse_start_exe_roundtrip(self):
        raw = build_tcp_command(TcpCommand.START_EXE)
        parsed = parse_tcp_command(raw)
        assert parsed is not None
        assert parsed.command == TcpCommand.START_EXE
        assert parsed.payload == ""

    def test_parse_update_txt_preserves_encoded_payload(self):
        raw = build_tcp_command(TcpCommand.UPDATE_TXT, "user1----pass1\nuser2----pass2")
        parsed = parse_tcp_command(raw)
        assert parsed is not None
        assert parsed.command == TcpCommand.UPDATE_TXT
        assert base64.b64decode(parsed.payload).decode("utf-8") == "user1----pass1\nuser2----pass2"

    def test_parse_delete_file_preserves_pipe_payload(self):
        raw = build_tcp_command(TcpCommand.DELETE_FILE, "a.txt|b.txt|c.log")
        parsed = parse_tcp_command(raw)
        assert parsed is not None
        assert parsed.command == TcpCommand.DELETE_FILE
        assert parsed.payload == "a.txt|b.txt|c.log"

    def test_parse_ext_set_config_preserves_filename_and_content(self):
        raw = build_tcp_command(TcpCommand.EXT_SET_CONFIG, "武器配置.txt|AK74|扩展")
        parsed = parse_tcp_command(raw)
        assert parsed is not None
        assert parsed.command == TcpCommand.EXT_SET_CONFIG
        assert parsed.payload == "武器配置.txt|AK74|扩展"

    def test_build_self_update_payload_includes_hash_and_size(self):
        payload = build_self_update_payload("TriangleAlpha-Slave.exe", b"ABC")

        parts = payload.split("|", 3)
        assert parts[0] == "TriangleAlpha-Slave.exe"
        assert parts[1].startswith("SHA256:")
        assert parts[2] == "SIZE:3"
        assert base64.b64decode(parts[3]) == b"ABC"


class TestUdpProtocolContracts:
    def test_ext_online_roundtrip_includes_extended_fields(self):
        raw = build_udp_ext_online(
            "VM-01",
            "Admin",
            45.2,
            60.1,
            "2.0.0",
            "A组",
            teammate_fill="1",
            weapon_config="AK74",
            level_threshold="18",
        )
        parsed = parse_udp_message(raw)
        assert parsed is not None
        assert parsed.machine_name == "VM-01"
        assert parsed.group == "A组"
        assert parsed.teammate_fill == "1"
        assert parsed.weapon_config == "AK74"
        assert parsed.level_threshold == "18"

    def test_account_sync_roundtrip(self):
        """ACCOUNT_SYNC 消息构建→解析往返测试"""
        accounts = [
            {"username": "u1", "level": 18, "jin_bi": "5000", "is_banned": False},
            {"username": "u2", "level": 5, "jin_bi": "100", "is_banned": True},
        ]
        payload = json.dumps(accounts, ensure_ascii=False, separators=(",", ":"))
        payload_b64 = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
        raw = build_udp_account_sync("VM-01", payload_b64)
        parsed = parse_udp_message(raw)
        assert parsed is not None
        assert parsed.type == UdpMessageType.ACCOUNT_SYNC
        assert parsed.machine_name == "VM-01"
        # 解码 payload 验证数据完整性
        decoded = json.loads(base64.b64decode(parsed.sync_payload).decode("utf-8"))
        assert len(decoded) == 2
        assert decoded[0]["username"] == "u1"
        assert decoded[1]["is_banned"] is True

    def test_account_sync_empty_payload(self):
        raw = "ACCOUNT_SYNC|VM-01|"
        parsed = parse_udp_message(raw)
        # 空 payload 应被拒绝（防止无效同步）
        assert parsed is None
