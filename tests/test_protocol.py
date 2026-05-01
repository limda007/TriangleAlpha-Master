from common.protocol import (
    TcpCommand,
    UdpMessageType,
    build_tcp_command,
    build_udp_account_sync,
    build_udp_ext_online,
    build_udp_online,
    build_udp_status,
    parse_tcp_command,
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

    def test_parse_ext_online_with_token_and_kami(self):
        msg = parse_udp_message("EXT_ONLINE|VM-01|Admin|45.2|60.1|1.0.0|A组|||||TOKEN123|KAMI456")
        assert msg is not None
        assert msg.token_key == "TOKEN123"
        assert msg.kami_code == "KAMI456"

    def test_parse_ext_online_with_vram(self):
        msg = parse_udp_message("EXT_ONLINE|VM-01|Admin|45.2|60.1|1.0.0|A组|||||TOKEN123|KAMI456|4200|6144")
        assert msg is not None
        assert msg.vram_used_mb == 4200
        assert msg.vram_total_mb == 6144

    def test_parse_ext_online_without_vram_backward_compat(self):
        msg = parse_udp_message("EXT_ONLINE|VM-01|Admin|45.2|60.1|1.0.0|A组|||||TOKEN123|KAMI456")
        assert msg is not None
        assert msg.vram_used_mb == 0
        assert msg.vram_total_mb == 0

    def test_parse_unknown_returns_none(self):
        assert parse_udp_message("GARBAGE|data") is None

    def test_parse_need_kami_returns_none(self):
        assert parse_udp_message("NEED_KAMI|VM-01") is None

    def test_parse_ext_online_invalid_cpu(self):
        """Invalid float in CPU field defaults to 0.0"""
        msg = parse_udp_message("EXT_ONLINE|VM-01|Admin|abc|60.1|1.0.0|A组")
        assert msg is not None
        assert msg.cpu_percent == 0.0
        assert msg.mem_percent == 60.1

    def test_parse_ext_online_negative_cpu(self):
        """Negative float is valid"""
        msg = parse_udp_message("EXT_ONLINE|VM-01|Admin|-1.5|60.1|1.0.0|A组")
        assert msg is not None
        assert msg.cpu_percent == -1.5

    def test_parse_account_sync_empty_payload(self):
        """Empty ACCOUNT_SYNC payload returns None"""
        assert parse_udp_message("ACCOUNT_SYNC|VM-01|") is None

    def test_parse_account_sync_valid(self):
        """Valid ACCOUNT_SYNC parses correctly"""
        msg = parse_udp_message("ACCOUNT_SYNC|VM-01|dGVzdA==")
        assert msg is not None
        assert msg.type == UdpMessageType.ACCOUNT_SYNC
        assert msg.sync_payload == "dGVzdA=="

    def test_parse_status_non_digit_level(self):
        """Non-digit level defaults to 0"""
        msg = parse_udp_message("STATUS|VM-01|运行中|abc|999|描述")
        assert msg is not None
        assert msg.level == 0

    def test_parse_status_with_status_text(self):
        """STATUS with 8 parts includes status_text"""
        msg = parse_udp_message("STATUS|VM-01|运行中|18|12450|正在升级|120|等待匹配")
        assert msg is not None
        assert msg.status_text == "等待匹配"
        assert msg.elapsed == "120"

    def test_parse_empty_string(self):
        """Empty string returns None"""
        assert parse_udp_message("") is None

    def test_parse_need_account(self):
        """NEED_ACCOUNT parses correctly"""
        msg = parse_udp_message("NEED_ACCOUNT|VM-01")
        assert msg is not None
        assert msg.type == UdpMessageType.NEED_ACCOUNT
        assert msg.machine_name == "VM-01"


class TestBuildUdp:
    def test_build_online(self):
        assert build_udp_online("VM-01", "Admin") == "ONLINE|VM-01|Admin"

    def test_build_ext_online_with_vram(self):
        result = build_udp_ext_online("VM-01", "Admin", 45.2, 60.1, "1.0.0", "A组",
                                       vram_used_mb=4200, vram_total_mb=6144)
        assert "|4200|6144" in result
        # Round-trip: parse it back
        msg = parse_udp_message(result)
        assert msg is not None
        assert msg.vram_used_mb == 4200
        assert msg.vram_total_mb == 6144

    def test_build_ext_online_default_vram(self):
        result = build_udp_ext_online("VM-01", "Admin", 0.0, 0.0, "1.0.0", "默认")
        assert result.endswith("|0|0")

    def test_build_status(self):
        result_default = build_udp_status("VM-01", "升级中", 18, "12450", "正在升级")
        assert result_default == "STATUS|VM-01|升级中|18|12450|正在升级|0|"
        result = build_udp_status("VM-01", "升级中", 18, "12450", "正在升级", "120")
        assert result == "STATUS|VM-01|升级中|18|12450|正在升级|120|"
        result_st = build_udp_status("VM-01", "升级中", 18, "12450", "正在升级", "120", "等待匹配")
        assert result_st == "STATUS|VM-01|升级中|18|12450|正在升级|120|等待匹配"


class TestBuildTcp:
    def test_start_exe(self):
        assert build_tcp_command(TcpCommand.START_EXE) == "STARTEXE|"

    def test_update_key(self):
        import base64

        cmd = build_tcp_command(TcpCommand.UPDATE_KEY, payload="MYKEY123")
        parts = cmd.split("|", 1)
        assert parts[0] == "UPDATEKEY"
        assert base64.b64decode(parts[1]).decode("utf-8") == "MYKEY123"


class TestParseTcp:
    def test_parse_start_exe(self):
        result = parse_tcp_command("STARTEXE|")
        assert result is not None
        assert result.command == TcpCommand.START_EXE
        assert result.payload == ""

    def test_parse_without_pipe(self):
        result = parse_tcp_command("STARTEXE")
        assert result is not None
        assert result.command == TcpCommand.START_EXE
        assert result.payload == ""

    def test_parse_unknown_command(self):
        assert parse_tcp_command("UNKNOWN|data") is None

    def test_parse_with_payload(self):
        result = parse_tcp_command("UPDATESELF|mydata")
        assert result is not None
        assert result.command == TcpCommand.UPDATE_SELF
        assert result.payload == "mydata"
