"""AccountInterface 账号表格回归测试。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest
from PyQt6.QtWidgets import QApplication, QHeaderView

from master.app.core.account_db import AccountDB
from master.app.view.account_interface import AccountInterface

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return cast(QApplication, app)


@pytest.fixture()
def account_interface(tmp_path: Path, qapp: QApplication):
    db = AccountDB(tmp_path / "accounts.db")
    widget = AccountInterface(db)
    widget.show()
    qapp.processEvents()
    yield widget, db
    widget.close()
    db.close()
    qapp.processEvents()


def test_status_column_uses_item_rendering(account_interface) -> None:
    widget, db = account_interface
    db.import_fresh("u1----p1----e1----ep1")
    widget._refreshTable()

    item = widget.table.item(0, 4)
    assert item is not None
    assert item.text() == "空闲中"
    assert widget.table.cellWidget(0, 4) is None


def test_table_uses_fixed_resize_modes_for_heavy_columns(account_interface) -> None:
    widget, _db = account_interface
    header = widget.table.horizontalHeader()

    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(4) == QHeaderView.ResizeMode.Fixed
    assert header.sectionResizeMode(8) == QHeaderView.ResizeMode.Fixed
