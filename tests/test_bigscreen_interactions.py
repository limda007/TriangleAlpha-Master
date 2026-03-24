"""BigScreenInterface 交互回归测试。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, call

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from common.models import NodeInfo
from common.protocol import TcpCommand
from master.app.common.style_sheet import StyleSheet
from master.app.core.account_db import AccountDB
from master.app.core.node_manager import NodeManager
from master.app.core.tcp_commander import TcpCommander
from master.app.view.bigscreen_interface import BigScreenInterface

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return cast(QApplication, app)


@pytest.fixture()
def bigscreen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp: QApplication):
    monkeypatch.setattr(StyleSheet, "apply", lambda self, widget: None, raising=False)
    node_manager = NodeManager()
    commander = TcpCommander()
    account_db = AccountDB(tmp_path / "accounts.db")
    widget = BigScreenInterface(node_manager, commander, account_db)
    widget.show()
    qapp.processEvents()
    yield widget, node_manager, commander, account_db
    widget.close()
    commander.stop()
    account_db.close()
    qapp.processEvents()


def test_selection_change_resets_and_refills_config_panel(bigscreen, qapp: QApplication) -> None:
    widget, node_manager, _commander, _account_db = bigscreen
    node_manager.nodes["VM-01"] = NodeInfo(
        machine_name="VM-01",
        ip="10.0.0.1",
        teammate_fill="1",
        weapon_config="AK74",
        level_threshold="20",
    )
    node_manager.nodes["VM-02"] = NodeInfo(machine_name="VM-02", ip="10.0.0.2")
    widget._refreshNodeTable()

    widget.table.selectRow(0)
    qapp.processEvents()
    assert widget._cfgTeammate.currentText() == "开启"
    assert widget._cfgWeapon.currentText() == "AK74"
    assert widget._cfgLevel.value() == 20

    widget.table.selectRow(1)
    qapp.processEvents()
    assert widget._cfgTeammate.currentText() == "关闭"
    assert widget._cfgWeapon.currentText() == "G17_不带药"
    assert widget._cfgLevel.value() == 18


def test_file_page_does_not_create_export_all_button(bigscreen) -> None:
    widget, _node_manager, _commander, _account_db = bigscreen

    assert widget.findChild(QWidget, "btnExportAll") is None


def test_one_click_start_pushes_config_only(
    bigscreen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一键分发文件只下发配置，不再分发账号"""
    widget, node_manager, commander, _account_db = bigscreen
    node_manager.nodes["VM-01"] = NodeInfo(machine_name="VM-01", ip="10.0.0.1")
    node_manager.nodes["VM-02"] = NodeInfo(machine_name="VM-02", ip="10.0.0.2")
    widget._refreshNodeTable()

    broadcast_mock = MagicMock()
    success_mock = MagicMock()
    monkeypatch.setattr(widget, "_confirmDangerous", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(commander, "broadcast", broadcast_mock)
    monkeypatch.setattr("master.app.view.bigscreen_interface.InfoBar.success", success_mock)

    widget._oneClickStart()

    expected = [
        call(["10.0.0.1", "10.0.0.2"], TcpCommand.EXT_SET_CONFIG, "补齐队友配置.txt|0"),
        call(["10.0.0.1", "10.0.0.2"], TcpCommand.EXT_SET_CONFIG, "武器配置.txt|G17_不带药"),
        call(["10.0.0.1", "10.0.0.2"], TcpCommand.EXT_SET_CONFIG, "下号等级.txt|18"),
        call(["10.0.0.1", "10.0.0.2"], TcpCommand.EXT_SET_CONFIG, "舔包次数.txt|8"),
    ]
    assert broadcast_mock.call_args_list == expected
    success_mock.assert_called_once()
