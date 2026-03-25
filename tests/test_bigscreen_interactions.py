"""BigScreenInterface 交互回归测试。"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, call

import pytest
from PyQt6.QtWidgets import QApplication, QHeaderView, QWidget

from common.models import NodeInfo
from common.protocol import ACCOUNT_RUNTIME_CLEANUP_PAYLOAD, TcpCommand, build_self_update_payload
from master.app.common.style_sheet import StyleSheet
from master.app.core.account_db import AccountDB
from master.app.core.node_manager import NodeManager
from master.app.core.tcp_commander import TcpCommander
from master.app.view.bigscreen_interface import BigScreenInterface, _supports_self_update_hash_payload

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


def test_account_panel_refresh_only_loads_idle_accounts(bigscreen) -> None:
    widget, _node_manager, _commander, account_db = bigscreen
    account_db.import_fresh("u1----p1\nu2----p2")
    account_db.allocate("VM-01")
    account_db._conn.execute(
        "UPDATE accounts SET last_login_at='2026-03-25 09:15:00' WHERE username='u2'"
    )
    account_db._conn.commit()

    widget._flushAccountRefresh()

    assert widget.accountTable.rowCount() == 1
    assert widget.accountTable.item(0, 0).text() == "u2"
    assert widget.accountTable.item(0, 6).text() == "03-25 09:15"
    header = widget.accountTable.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Fixed


def test_one_click_start_pushes_selected_files(
    bigscreen,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """一键分发文件会把选中文件作为配置下发到所有在线节点。"""
    widget, node_manager, commander, _account_db = bigscreen
    node_manager.nodes["VM-01"] = NodeInfo(machine_name="VM-01", ip="10.0.0.1")
    node_manager.nodes["VM-02"] = NodeInfo(machine_name="VM-02", ip="10.0.0.2")
    widget._refreshNodeTable()

    file1 = tmp_path / "补齐队友配置.txt"
    file2 = tmp_path / "下号等级.txt"
    file1.write_text("0", encoding="utf-8")
    file2.write_text("18", encoding="utf-8")

    broadcast_mock = MagicMock()
    success_mock = MagicMock()
    monkeypatch.setattr(widget, "_confirmDangerous", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(commander, "broadcast", broadcast_mock)
    monkeypatch.setattr("master.app.view.bigscreen_interface.InfoBar.success", success_mock)
    monkeypatch.setattr(
        "master.app.view.bigscreen_interface.QFileDialog.getOpenFileNames",
        lambda *_args, **_kwargs: ([str(file1), str(file2)], ""),
    )

    widget._oneClickStart()

    expected = [
        call(
            ["10.0.0.1", "10.0.0.2"],
            TcpCommand.EXT_SET_CONFIG,
            f"{file1.name}|BASE64:{base64.b64encode(file1.read_bytes()).decode('ascii')}",
        ),
        call(
            ["10.0.0.1", "10.0.0.2"],
            TcpCommand.EXT_SET_CONFIG,
            f"{file2.name}|BASE64:{base64.b64encode(file2.read_bytes()).decode('ascii')}",
        ),
    ]
    assert broadcast_mock.call_args_list == expected
    success_mock.assert_called_once()


def test_one_click_start_self_update_uses_legacy_payload_for_old_nodes(
    bigscreen,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget, node_manager, commander, _account_db = bigscreen
    node_manager.nodes["VM-OLD"] = NodeInfo(machine_name="VM-OLD", ip="10.0.0.1", slave_version="1.0.53")
    node_manager.nodes["VM-NEW"] = NodeInfo(machine_name="VM-NEW", ip="10.0.0.2", slave_version="1.0.54")
    widget._refreshNodeTable()

    exe_path = tmp_path / "TriangleAlpha-Slave.exe"
    exe_path.write_bytes(b"new-slave-binary")

    broadcast_mock = MagicMock()
    monkeypatch.setattr(widget, "_confirmDangerous", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(commander, "broadcast", broadcast_mock)
    monkeypatch.setattr("master.app.view.bigscreen_interface.InfoBar.success", MagicMock())
    monkeypatch.setattr(
        "master.app.view.bigscreen_interface.QFileDialog.getOpenFileNames",
        lambda *_args, **_kwargs: ([str(exe_path)], ""),
    )

    widget._oneClickStart()

    legacy_payload = f"{exe_path.name}|{base64.b64encode(exe_path.read_bytes()).decode('ascii')}"
    modern_payload = build_self_update_payload(exe_path.name, exe_path.read_bytes())
    assert broadcast_mock.call_args_list == [
        call(["10.0.0.1"], TcpCommand.UPDATE_SELF, legacy_payload),
        call(["10.0.0.2"], TcpCommand.UPDATE_SELF, modern_payload),
    ]


def test_supports_self_update_hash_payload_requires_1_0_54_or_newer() -> None:
    assert not _supports_self_update_hash_payload("")
    assert not _supports_self_update_hash_payload("1.0.53")
    assert _supports_self_update_hash_payload("1.0.54")
    assert _supports_self_update_hash_payload("1.1.0")


def test_clean_standalone_accounts_cleans_runtime_files(
    bigscreen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget, node_manager, commander, _account_db = bigscreen
    node_manager.nodes["VM-01"] = NodeInfo(machine_name="VM-01", ip="10.0.0.1")
    widget._refreshNodeTable()

    broadcast_mock = MagicMock()
    monkeypatch.setattr(widget, "_confirmDangerous", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(commander, "broadcast", broadcast_mock)

    widget._cleanStandaloneAccounts()

    broadcast_mock.assert_called_once_with(
        ["10.0.0.1"],
        TcpCommand.DELETE_FILE,
        ACCOUNT_RUNTIME_CLEANUP_PAYLOAD,
    )
