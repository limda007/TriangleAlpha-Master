"""TCP 指令构建与发送策略集成测试"""

import base64

import pytest
from PyQt6.QtWidgets import QApplication

from common.protocol import TcpCommand, build_tcp_command
from master.app.core.tcp_commander import TcpCommander, _TcpSendTask


@pytest.fixture(scope="session")
def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


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

    def test_build_push_kami_encodes_base64(self):
        cmd = build_tcp_command(TcpCommand.PUSH_KAMI, payload="KAMI123")
        parts = cmd.split("|", 1)
        assert parts[0] == "PUSHKAMI"
        assert base64.b64decode(parts[1]).decode("utf-8") == "KAMI123"

    def test_build_delete_file(self):
        cmd = build_tcp_command(TcpCommand.DELETE_FILE, payload="old.txt|temp.log")
        assert cmd == "DELETEFILE|old.txt|temp.log"

    def test_build_ext_set_group(self):
        cmd = build_tcp_command(TcpCommand.EXT_SET_GROUP, payload="A组")
        assert cmd == "EXT_SETGROUP|A组"

    def test_build_ext_set_config_keeps_filename_and_content_contract(self):
        cmd = build_tcp_command(TcpCommand.EXT_SET_CONFIG, payload="configs/runtime.ini|mode=managed")
        assert cmd == "EXT_SETCONFIG|configs/runtime.ini|mode=managed"


# ── 17.7: ACK 严格模式按 client_type 自适应 ─────────────────────────


class TestSendTaskStrictAckPolicy:
    """_TcpSendTask 决定是否对 ACK 缺失抛错的策略矩阵.

    决策表:
    | explicit require_ack | resolver(ip) 返回值      | 期望 strict |
    | True                 | 任意                      | True       |
    | False                | 'astar_agent'            | True       |
    | False                | 'legacy_slave' / 其他     | False      |
    | False                | None                     | False      |
    | False                | resolver 抛异常           | False      |
    | False                | 没有 resolver             | False      |
    """

    def _make_task(self, **kwargs) -> _TcpSendTask:
        return _TcpSendTask(
            "10.0.0.1",
            "STARTEXE|",
            commander=None,  # type: ignore[arg-type]
            **kwargs,
        )

    def test_explicit_require_ack_true_overrides_everything(self, _qapp):
        task = self._make_task(require_ack=True)
        assert task._expects_strict_ack() is True

    def test_default_no_resolver_keeps_legacy_compat(self, _qapp):
        task = self._make_task()
        assert task._expects_strict_ack() is False

    def test_resolver_returns_astar_agent_promotes_to_strict(self, _qapp):
        task = self._make_task(client_type_resolver=lambda ip: "astar_agent")
        assert task._expects_strict_ack() is True

    def test_resolver_returns_legacy_slave_keeps_compat_mode(self, _qapp):
        task = self._make_task(client_type_resolver=lambda ip: "legacy_slave")
        assert task._expects_strict_ack() is False

    def test_resolver_returns_none_keeps_compat_mode(self, _qapp):
        task = self._make_task(client_type_resolver=lambda ip: None)
        assert task._expects_strict_ack() is False

    def test_resolver_raises_falls_back_to_compat_mode(self, _qapp):
        def boom(ip: str) -> str | None:
            raise RuntimeError("node manager gone")

        task = self._make_task(client_type_resolver=boom)
        assert task._expects_strict_ack() is False

    def test_explicit_true_still_strict_even_for_legacy_slave(self, _qapp):
        """explicit require_ack=True 必须不被 resolver 反转."""
        task = self._make_task(
            require_ack=True,
            client_type_resolver=lambda ip: "legacy_slave",
        )
        assert task._expects_strict_ack() is True


class TestCommanderResolverInjection:
    """TcpCommander 持有 resolver, 并把它透传给 _TcpSendTask."""

    def test_set_client_type_resolver_is_used_by_send_tasks(self, _qapp, monkeypatch):
        commander = TcpCommander()
        captured: list[_TcpSendTask] = []

        original_start = commander._pool.start

        def capture_start(task, *args, **kwargs):
            captured.append(task)
            # 不真正调度

        monkeypatch.setattr(commander._pool, "start", capture_start)
        commander.set_client_type_resolver(lambda ip: "astar_agent")
        commander.send("10.0.0.1", TcpCommand.START_EXE)

        assert len(captured) == 1
        assert captured[0]._expects_strict_ack() is True

    def test_commander_without_resolver_keeps_legacy_compat(self, _qapp, monkeypatch):
        commander = TcpCommander()
        captured: list[_TcpSendTask] = []
        monkeypatch.setattr(commander._pool, "start", lambda task: captured.append(task))

        commander.send("10.0.0.1", TcpCommand.START_EXE)

        assert len(captured) == 1
        assert captured[0]._expects_strict_ack() is False

    def test_broadcast_uses_resolver_per_ip(self, _qapp, monkeypatch):
        """broadcast 也透传 resolver, 每个任务独立判断."""
        commander = TcpCommander()
        captured: list[_TcpSendTask] = []
        monkeypatch.setattr(commander._pool, "start", lambda task: captured.append(task))

        type_map = {"10.0.0.1": "astar_agent", "10.0.0.2": "legacy_slave"}
        commander.set_client_type_resolver(lambda ip: type_map.get(ip))
        commander.broadcast(["10.0.0.1", "10.0.0.2"], TcpCommand.START_EXE)

        assert len(captured) == 2
        ips_strict = {t._ip: t._expects_strict_ack() for t in captured}
        assert ips_strict == {"10.0.0.1": True, "10.0.0.2": False}

