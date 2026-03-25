"""AccountInterface 账号表格回归测试。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest
from PyQt6.QtWidgets import QApplication

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
    db._conn.execute(
        "UPDATE accounts SET last_login_at='2026-03-25 14:32:00' WHERE username='u1'"
    )
    db._conn.commit()
    widget._refreshTable()

    item = widget.table.item(0, 4)
    assert item is not None
    assert item.text() == "空闲中"
    assert widget.table.cellWidget(0, 4) is None
    assert widget.table.item(0, 8).text() == "03-25 14:32"
