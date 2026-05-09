# AccountDB 工作线程化 — 实施计划

**Date:** 2026-05-09
**Status:** Draft
**Scope:** 将 SQLite 操作从 Qt 主线程移至专用工作线程，消除大规模账号操作时的 UI 卡顿

---

## 动机

当前 `AccountDB` 所有 SQL 操作在 Qt 主线程同步执行。5000+ 账号时：
- `_refresh_counts()` GROUP BY 全表扫描 → 1-5ms
- `upsert_from_sync` 逐条 INSERT/UPDATE → 50-200ms
- `export_completed` / `export_all` 全表查询 → 100-500ms

这些操作阻塞 Qt 事件循环，导致 UI 掉帧、右键菜单延迟。

Phase 2.1 的增量计数已将热路径（`allocate`/`release`/`complete`）的 `_refresh_counts` 消除，但其余操作仍直接访问 SQLite。

## 目标

- 所有 SQLite 读写操作在专用 `QThread` 中执行
- Qt 主线程通过 signal/slot 获取结果，不被阻塞
- 增量计数器的线程安全保护
- 现有 API 接口保持兼容
- `make check` 全绿 + 477 测试通过

## 设计方案

### 架构

```
MainWindow (Qt 主线程)
    │
    ├─ signal: allocate_request(machine_name)
    │            release_request(machine_name)
    │            upsert_sync_request(...)
    │            get_all_accounts_request()
    │            ...
    │
    ▼
AccountDBWorker (QObject, moved to QThread)
    │
    ├─ 持有独立 sqlite3.Connection
    ├─ 接收 slot → 执行 SQL → emit 结果 signal
    │
    ▼
MainWindow
    ├─ slot: on_allocate_result(AccountInfo | None)
    ├─ slot: on_accounts_loaded(list[AccountInfo])
    ├─ slot: on_pool_changed()
    ...
```

### 线程模型

```
┌─────────────────────────────────┐
│  QThread("account-db-worker")   │
│  ┌───────────────────────────┐  │
│  │  AccountDBWorker           │  │
│  │  - _conn: sqlite3.Connection (独立) │
│  │  - 增量计数器 (RLock 保护)  │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
         ▲ signal/slot (Qt::QueuedConnection)
         │
┌────────┴────────────────────────┐
│  MainThread (Qt 主线程)         │
│  - AccountDB (API 代理)         │
│  - 所有调用返回 void             │
│  - 结果通过 signal 异步返回       │
└─────────────────────────────────┘
```

### 关键决策

1. **独立连接**: Worker 持有自己的 `sqlite3.Connection`（`check_same_thread=False`），不与主线程共享。避免 SQLite 跨线程锁冲突。

2. **异步 API**: 所有现有同步方法（`allocate`→`AccountInfo|None`）改为异步（`allocate_request`→`void`），结果通过 `pyqtSignal` 回传。

3. **向后兼容**: 保留 `AccountDB` 作为代理层 —— 外部调用者（BigScreenInterface, AccountInterface 等）无需感知内部线程模型变更。

4. **增量计数器线程安全**: 当前 `_inc_count`/`_dec_count`/`_xfer_count` 在 worker 线程中串行执行（SQLite 连接串行化），无需额外锁。若未来引入多 worker，才需要 `threading.RLock`。

## 文件改动

### 新增：`src/master/app/core/account_db_worker.py`

```python
"""AccountDB 工作线程 — 在独立 QThread 中执行所有 SQLite 操作"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from common.models import AccountInfo, AccountStatus


class AccountDBWorker(QObject):
    """QObject worker, 运行在专用 QThread 中"""

    # ── 结果信号 (emit → 主线程) ──
    allocate_result = pyqtSignal(object)      # AccountInfo | None
    release_done = pyqtSignal(str)             # machine_name
    pool_changed = pyqtSignal()
    accounts_loaded = pyqtSignal(list)         # list[AccountInfo]
    upsert_sync_done = pyqtSignal(int, int)    # (inserted, updated)
    import_done = pyqtSignal(int, int)         # (inserted, skipped)
    export_result = pyqtSignal(str)            # exported text
    error_occurred = pyqtSignal(str)           # error message

    def __init__(self, db_path: str, parent: QObject | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

        # 增量计数
        self._total = 0
        self._available = 0
        self._in_use = 0
        self._completed = 0

    def init_db(self) -> None:
        """在工作线程中初始化数据库连接（由 QThread.started 触发）"""
        self._conn = sqlite3.connect(
            self._db_path, timeout=10, isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)  # _SCHEMA 复用自 account_db.py
        self._refresh_counts()

    # ── 请求 slots ──

    @pyqtSlot(str)
    def allocate(self, machine_name: str) -> None:
        """同 AccountDB.allocate, 结果通过 allocate_result 信号返回"""
        try:
            result = self._do_allocate(machine_name)
            self.allocate_result.emit(result)
        except Exception as e:
            self.error_occurred.emit(str(e))

    @pyqtSlot(str)
    def release(self, machine_name: str) -> None:
        try:
            self._do_release(machine_name)
            self.release_done.emit(machine_name)
        except Exception as e:
            self.error_occurred.emit(str(e))

    # ... (其余方法同模式)

    def _do_allocate(self, machine_name: str) -> AccountInfo | None:
        """实际 SQL 逻辑（从 AccountDB.allocate 迁移）"""
        # 同原逻辑，使用 self._conn
        ...
```

### 修改：`src/master/app/core/account_db.py`

原 `AccountDB` 变为 thin proxy：

```python
class AccountDB(QObject):
    """线程安全代理 — 所有请求转发到 AccountDBWorker"""

    pool_changed = pyqtSignal()

    def __init__(self, db_path: str | Path, parent: QObject | None = None):
        super().__init__(parent)
        self._worker = AccountDBWorker(str(db_path))
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)

        # 转发 worker 信号
        self._worker.pool_changed.connect(self.pool_changed)
        self._worker.allocate_result.connect(self._on_allocate_done)
        # ...

        self._thread.started.connect(self._worker.init_db)
        self._thread.start()

        # 异步结果暂存
        self._pending_allocate: dict[str, AccountInfo | None] = {}

    def allocate(self, machine_name: str) -> AccountInfo | None:
        """同步兼容：通过 QMetaObject.invokeMethod 调用并等待"""
        # 方案 A: 使用 QEventLoop 同步等待
        # 方案 B: 保留同步 API 但标记为 deprecated
        ...
```

### 修改：调用方（BigScreenInterface, AccountInterface, KamiInterface）

需要适配异步 API。核心变更：

```python
# Before
account = self.accountPool.allocate(machine_name)

# After (方案 A — 最小改动)
# accountPool.allocate 内部用 QEventLoop 同步等待，调用方代码不变

# After (方案 B — 完全异步)
self.accountPool.allocate_request.emit(machine_name)
# 结果在 self.on_allocate_result(account) 中处理
```

**推荐方案 A** — 对调用方零改动，渐进迁移。

## 风险评估

| 风险 | 级别 | 缓解措施 |
|------|------|---------|
| SQLite 跨线程死锁 | HIGH | WAL 模式 + 独立连接 + `check_same_thread=False` |
| 信号顺序错乱 | MED | Qt::QueuedConnection 保证 FIFO |
| 异步导致数据竞争 | MED | 所有读写串行在 worker 线程；计数器单线程访问 |
| 现有测试失败 | MED | 逐测试适配；worker 可通过同步模式运行 |
| DB 关闭时序 | LOW | `close()` 先停 worker 线程，等 `QThread.finished` |

## 实施步骤

| Step | 内容 | 估时 |
|------|------|------|
| 1 | 创建 `AccountDBWorker` 类，迁移 `allocate`/`release`/`complete` | 30min |
| 2 | AccountDB 改为 proxy，QEventLoop 同步等待 | 20min |
| 3 | 迁移剩余方法（`upsert_from_sync`, `get_all_accounts`, `export` 等） | 30min |
| 4 | 迁移增量计数器到 worker 线程 | 15min |
| 5 | 适配所有调用方 | 20min |
| 6 | 运行全测试套件，修复失败用例 | 30min |
| 7 | 100+ 节点实机压力测试 | 30min |

## 验证标准

- [ ] 所有 477 现有测试通过
- [ ] 主线程在 5000 账号导入期间无 >16ms 阻塞
- [ ] `allocate`/`release` 端到端延迟 <5ms
- [ ] 连续 1000 次 `allocate` + `release` 后计数器与实际一致
- [ ] 程序正常关闭无 crash
- [ ] `make check` 全绿

## 备选方案

**方案 C（最小化）: 仅将 `_refresh_counts` 改为 QTimer 异步刷新**
- 改动极小（~10 行）
- 但其他阻塞操作（`upsert_from_sync` 等）仍卡 UI
- 适合作为 Phase 2.1 的快速跟进，而非完整解决方案
