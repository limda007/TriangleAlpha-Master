from common.protocol import (
    TcpCommand,
    UdpMessageType,
    build_tcp_command,
    build_udp_online,
    build_udp_status,
    parse_udp_message,
)


class TestParseUdp:
    def test_parse_online(self):
        msg = parse_udp_message("ONLINE|VM-01|Admin")
        assert msg is not None
        assert msg.type == UdpMessageType.ONLINE
        assert msg.machine_name == "VM-01"
        assert msg.user_name == "Admin"

    def test_parse_status(self):
        msg = parse_udp_message("STATUS|VM-01|升级中|18|12450|正在升级")
        assert msg is not None
        assert msg.type == UdpMessageType.STATUS
        assert msg.level == 18
        assert msg.jin_bi == "12450"
        assert msg.desc == "正在升级"
        assert msg.elapsed == "0"  # 6 段兼容默认值

    def test_parse_status_with_elapsed(self):
        msg = parse_udp_message("STATUS|VM-01|升级中|18|12450|正在升级|120")
        assert msg is not None
        assert msg.elapsed == "120"

    def test_parse_offline(self):
        msg = parse_udp_message("OFFLINE|VM-01")
        assert msg is not None
        assert msg.type == UdpMessageType.OFFLINE

    def test_parse_ext_online(self):
        msg = parse_udp_message("EXT_ONLINE|VM-01|Admin|45.2|60.1|1.0.0|A组")
        assert msg is not None
        assert msg.type == UdpMessageType.EXT_ONLINE
        assert msg.cpu_percent == 45.2
        assert msg.group == "A组"

    def test_parse_unknown_returns_none(self):
        assert parse_udp_message("GARBAGE|data") is None


class TestBuildUdp:
    def test_build_online(self):
        assert build_udp_online("VM-01", "Admin") == "ONLINE|VM-01|Admin"

    def test_build_status(self):
        assert build_udp_status("VM-01", "升级中", 18, "12450", "正在升级") == "STATUS|VM-01|升级中|18|12450|正在升级|0"
        result = build_udp_status("VM-01", "升级中", 18, "12450", "正在升级", "120")
        assert result == "STATUS|VM-01|升级中|18|12450|正在升级|120"


class TestBuildTcp:
    def test_start_exe(self):
        assert build_tcp_command(TcpCommand.START_EXE) == "STARTEXE|"

    def test_update_key(self):
        import base64

        cmd = build_tcp_command(TcpCommand.UPDATE_KEY, payload="MYKEY123")
        parts = cmd.split("|", 1)
        assert parts[0] == "UPDATEKEY"
        assert base64.b64decode(parts[1]).decode("utf-8") == "MYKEY123"
