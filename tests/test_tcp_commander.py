"""TCP 指令构建集成测试"""

import base64

from common.protocol import TcpCommand, build_tcp_command


class TestTcpCommandBuilding:
    def test_build_start_exe(self):
        assert build_tcp_command(TcpCommand.START_EXE) == "STARTEXE|"

    def test_build_stop_exe(self):
        assert build_tcp_command(TcpCommand.STOP_EXE) == "STOPEXE|"

    def test_build_reboot_pc(self):
        assert build_tcp_command(TcpCommand.REBOOT_PC) == "REBOOTPC|"

    def test_build_update_txt_encodes_base64(self):
        cmd = build_tcp_command(TcpCommand.UPDATE_TXT, payload="user1----pass1")
        parts = cmd.split("|", 1)
        assert parts[0] == "UPDATETXT"
        decoded = base64.b64decode(parts[1]).decode("utf-8")
        assert decoded == "user1----pass1"

    def test_build_update_key_encodes_base64(self):
        cmd = build_tcp_command(TcpCommand.UPDATE_KEY, payload="KEY123")
        parts = cmd.split("|", 1)
        assert parts[0] == "UPDATEKEY"
        assert base64.b64decode(parts[1]).decode("utf-8") == "KEY123"

    def test_build_delete_file(self):
        cmd = build_tcp_command(TcpCommand.DELETE_FILE, payload="old.txt|temp.log")
        assert cmd == "DELETEFILE|old.txt|temp.log"

    def test_build_ext_set_group(self):
        cmd = build_tcp_command(TcpCommand.EXT_SET_GROUP, payload="A组")
        assert cmd == "EXT_SETGROUP|A组"

    def test_build_ext_query(self):
        assert build_tcp_command(TcpCommand.EXT_QUERY) == "EXT_QUERY|"
