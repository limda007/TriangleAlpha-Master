# Slave 日志上报可靠性修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 slave 日志上报的两个可靠性问题：stderr 未捕获、队列满静默丢弃。同时扩容队列降低溢出概率。

**Architecture:** 扩展 `_TeeWriter` 同时拦截 stdout 和 stderr，增加线程安全的丢弃计数器；`LogReporter` 周期性检查丢弃计数并向 master 发送告警消息（同时本地 logging 兜底）；队列容量从 1000 提升到 5000 降低溢出概率。

**Tech Stack:** Python 3.14, asyncio, queue.Queue, threading.Lock, logging

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/slave/log_reporter.py` | Modify | 增加 stderr 拦截、丢弃计数、队列扩容 |
| `tests/test_log_reporter.py` | **Create** | 日志上报可靠性测试 |

---

### Task 1: _TeeWriter 丢弃计数器（线程安全）

**Files:**
- Create: `tests/test_log_reporter.py`
- Modify: `src/slave/log_reporter.py:134-166`

- [ ] **Step 1: 写丢弃计数测试**

```python
# tests/test_log_reporter.py
"""日志上报可靠性测试。"""
from __future__ import annotations

import queue as thread_queue

from slave.log_reporter import _TeeWriter


class TestTeeWriterDropCount:
    """队列满时应记录丢弃数而非静默丢弃。"""

    def test_no_drops_when_queue_has_space(self):
        q: thread_queue.Queue[str] = thread_queue.Queue(maxsize=100)
        writer = _TeeWriter(None, q, "VM-01")
        writer.write("hello\n")
        assert writer.drop_count == 0
        assert q.qsize() == 1

    def test_drops_counted_when_queue_full(self):
        q: thread_queue.Queue[str] = thread_queue.Queue(maxsize=1)
        writer = _TeeWriter(None, q, "VM-01")
        writer.write("line1\n")  # 入队成功
        writer.write("line2\n")  # 队列满，应丢弃并计数
        writer.write("line3\n")  # 再次丢弃
        assert writer.drop_count == 2

    def test_reset_drop_count(self):
        q: thread_queue.Queue[str] = thread_queue.Queue(maxsize=1)
        writer = _TeeWriter(None, q, "VM-01")
        writer.write("line1\n")
        writer.write("line2\n")  # 丢弃 1 条
        assert writer.drop_count == 1
        count = writer.reset_drop_count()
        assert count == 1
        assert writer.drop_count == 0

    def test_drop_count_is_thread_safe(self):
        """并发写入时丢弃计数不丢失。"""
        import threading
        q: thread_queue.Queue[str] = thread_queue.Queue(maxsize=1)
        writer = _TeeWriter(None, q, "VM-01")
        writer.write("fill\n")  # 填满队列
        barrier = threading.Barrier(10)

        def write_many():
            barrier.wait()
            for _ in range(100):
                writer.write("x\n")

        threads = [threading.Thread(target=write_many) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert writer.drop_count == 1000  # 10 线程 × 100 次
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_log_reporter.py::TestTeeWriterDropCount -v`
Expected: FAIL — `AttributeError: '_TeeWriter' object has no attribute 'drop_count'`

- [ ] **Step 3: 实现线程安全的丢弃计数器**

修改 `src/slave/log_reporter.py`：

**3a. 新增 import**（文件顶部）：

```python
import threading
```

**3b. `_TeeWriter.__init__` 新增属性**（line 146 之后）：

```python
self._drop_count: int = 0
self._drop_lock = threading.Lock()
```

**3c. `_TeeWriter.write` 替换静默 suppress 为计数**（原 line 164-165）：

```python
# 旧代码:
#     with contextlib.suppress(thread_queue.Full):
#         self._queue.put_nowait(msg)
# 新代码:
try:
    self._queue.put_nowait(msg)
except thread_queue.Full:
    with self._drop_lock:
        self._drop_count += 1
```

**3d. 新增属性和方法**：

```python
@property
def drop_count(self) -> int:
    return self._drop_count

def reset_drop_count(self) -> int:
    """返回当前丢弃数并归零（线程安全）。"""
    with self._drop_lock:
        count = self._drop_count
        self._drop_count = 0
        return count
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_log_reporter.py::TestTeeWriterDropCount -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/slave/log_reporter.py tests/test_log_reporter.py
git commit -m "✨ feat(log-reporter): 线程安全的丢弃计数器替代静默丢弃"
```

---

### Task 2: stderr 拦截

**Files:**
- Modify: `tests/test_log_reporter.py` (追加)
- Modify: `src/slave/log_reporter.py:33-36,111-112,134-166`

- [ ] **Step 1: 写 stderr 拦截测试**

在 `tests/test_log_reporter.py` 末尾追加：

```python
import sys


class TestStderrCapture:
    """install() 应同时拦截 stdout 和 stderr。"""

    def test_install_replaces_stderr(self):
        from slave.log_reporter import LogReporter
        original_stderr = sys.stderr
        reporter = LogReporter("127.0.0.1", "VM-01")
        reporter.install()
        try:
            assert sys.stderr is not original_stderr
            assert isinstance(sys.stderr, _TeeWriter)
        finally:
            reporter._restore_streams()

    def test_stderr_output_enters_queue_as_error(self):
        from slave.log_reporter import LogReporter
        reporter = LogReporter("127.0.0.1", "VM-01")
        reporter.install()
        try:
            sys.stderr.write("something broke\n")
            msg = reporter._queue.get_nowait()
            assert "something broke" in msg
            assert "|ERROR|" in msg  # stderr 输出默认标记为 ERROR
        finally:
            reporter._restore_streams()

    def test_stderr_warn_keyword_stays_error(self):
        """stderr 中含 WARN 关键字时，级别仍应为 ERROR（is_stderr 是下限）。"""
        from slave.log_reporter import LogReporter
        reporter = LogReporter("127.0.0.1", "VM-01")
        reporter.install()
        try:
            sys.stderr.write("WARNING: deprecation\n")
            msg = reporter._queue.get_nowait()
            assert "|ERROR|" in msg  # stderr 下限为 ERROR，不被 WARN 降级
        finally:
            reporter._restore_streams()

    def test_stop_restores_stderr(self):
        import asyncio
        from slave.log_reporter import LogReporter
        original_stderr = sys.stderr
        reporter = LogReporter("127.0.0.1", "VM-01")
        reporter.install()
        try:
            asyncio.run(reporter.stop())
            assert sys.stderr is original_stderr
        finally:
            # 兜底恢复，防止断言失败时污染后续测试
            reporter._restore_streams()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_log_reporter.py::TestStderrCapture -v`
Expected: FAIL — `AttributeError: 'LogReporter' object has no attribute '_restore_streams'` 或 stderr 未被替换

- [ ] **Step 3: 实现 stderr 拦截**

修改 `src/slave/log_reporter.py`：

**3a. `_TeeWriter.__init__` 新增 `is_stderr` 参数：**

```python
def __init__(self, original: IO[str] | None, q: thread_queue.Queue[str],
             machine_name: str, *, is_stderr: bool = False):
    self._original = original
    self._queue = q
    self._machine_name = machine_name
    self._is_stderr = is_stderr
    self.encoding: str = getattr(original, "encoding", "utf-8") or "utf-8"
    self._drop_count: int = 0
    self._drop_lock = threading.Lock()
```

**3b. `_TeeWriter.write` 级别推断 — is_stderr 作为下限：**

将级别推断逻辑改为：

```python
level = "INFO"
if "[错误]" in stripped or "[异常]" in stripped or "ERROR" in stripped:
    level = "ERROR"
elif "[警告]" in stripped or "WARN" in stripped:
    level = "WARN"
# stderr 输出的级别下限为 ERROR（不被 WARN 关键字降级）
if self._is_stderr and level != "ERROR":
    level = "ERROR"
```

**3c. `LogReporter.__init__` 新增 `_original_stderr` 属性：**

```python
self._original_stderr: IO[str] | None = None
```

**3d. `install()` 同时替换 stderr：**

```python
def install(self) -> None:
    """安装 stdout/stderr 拦截器"""
    self._original_stdout = sys.stdout
    self._original_stderr = sys.stderr
    sys.stdout = _TeeWriter(self._original_stdout, self._queue, self._machine_name)  # type: ignore[assignment]
    sys.stderr = _TeeWriter(self._original_stderr, self._queue, self._machine_name, is_stderr=True)  # type: ignore[assignment]
```

**3e. 新增 `_restore_streams()` 并修改 `stop()`：**

```python
def _restore_streams(self) -> None:
    """恢复原始 stdout/stderr。"""
    if self._original_stdout is not None:
        sys.stdout = self._original_stdout
    if self._original_stderr is not None:
        sys.stderr = self._original_stderr
```

`stop()` 中将原来的 `if self._original_stdout...` 替换为调用 `_restore_streams()`：

```python
async def stop(self) -> None:
    self._running = False
    self._restore_streams()
    # ... 后续不变（从 while not self._queue.empty() 开始）
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_log_reporter.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/slave/log_reporter.py tests/test_log_reporter.py
git commit -m "✨ feat(log-reporter): 拦截 stderr 并以 ERROR 级别上报 master"
```

---

### Task 3: 丢弃告警上报 + 队列扩容

**Files:**
- Modify: `tests/test_log_reporter.py` (追加)
- Modify: `src/slave/log_reporter.py:25,46-67`

- [ ] **Step 1: 写丢弃告警和队列容量测试**

在 `tests/test_log_reporter.py` 末尾追加：

```python
class TestQueueCapacity:
    """队列容量应为 5000。"""

    def test_queue_maxsize_is_5000(self):
        from slave.log_reporter import LogReporter
        reporter = LogReporter("127.0.0.1", "VM-01")
        assert reporter._queue.maxsize == 5000


class TestDropWarning:
    """丢弃告警应被注入队列发送给 master。"""

    def test_drop_warning_injected_when_drops_exist(self):
        """当有丢弃时，_check_and_warn_drops 应将告警消息注入队列。"""
        from slave.log_reporter import LogReporter
        reporter = LogReporter("127.0.0.1", "VM-01")
        reporter.install()
        try:
            # 模拟 _TeeWriter 上有丢弃
            stdout_tee = sys.stdout
            assert isinstance(stdout_tee, _TeeWriter)
            stdout_tee._drop_count = 42

            # 调用 _check_and_warn_drops 应注入告警
            reporter._check_and_warn_drops()
            assert stdout_tee.drop_count == 0  # 已重置

            # 队列中应有告警消息
            found_warning = False
            while not reporter._queue.empty():
                msg = reporter._queue.get_nowait()
                if "丢弃" in msg and "42" in msg:
                    found_warning = True
                    break
            assert found_warning, "应有包含丢弃数量的告警消息"
        finally:
            reporter._restore_streams()

    def test_drop_warning_has_local_fallback(self, caplog):
        """丢弃告警应同时写入本地 logging（防止告警本身被队列丢弃）。"""
        import logging
        from slave.log_reporter import LogReporter
        reporter = LogReporter("127.0.0.1", "VM-01")
        reporter.install()
        try:
            stdout_tee = sys.stdout
            assert isinstance(stdout_tee, _TeeWriter)
            stdout_tee._drop_count = 10

            with caplog.at_level(logging.WARNING):
                reporter._check_and_warn_drops()
            assert any("丢弃" in r.message and "10" in r.message for r in caplog.records)
        finally:
            reporter._restore_streams()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_log_reporter.py::TestQueueCapacity tests/test_log_reporter.py::TestDropWarning -v`
Expected: FAIL — queue maxsize 不是 5000 / `_check_and_warn_drops` 不存在

- [ ] **Step 3: 实现队列扩容和丢弃告警**

修改 `src/slave/log_reporter.py`：

**3a. 队列扩容**（line 25）：

```python
# 旧: self._queue: thread_queue.Queue[str] = thread_queue.Queue(maxsize=1000)
self._queue: thread_queue.Queue[str] = thread_queue.Queue(maxsize=5000)
```

**3b. 新增 `_check_and_warn_drops` 方法**（在 `_send_lines` 之后）：

```python
def _check_and_warn_drops(self) -> None:
    """检查 stdout/stderr tee writer 的丢弃计数，有丢弃时注入告警。"""
    total_drops = 0
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, _TeeWriter):
            total_drops += stream.reset_drop_count()
    if total_drops > 0:
        ts = datetime.now().strftime("%H:%M:%S")
        warn_msg = f"LOG|{self._machine_name}|{ts}|WARN|[日志丢弃] 队列溢出，丢弃 {total_drops} 条日志"
        # 本地 logging 兜底（即使队列也满了，至少本地可见）
        logging.getLogger("trianglealpha.log_reporter").warning(
            "[日志丢弃] 队列溢出，丢弃 %d 条日志", total_drops,
        )
        with contextlib.suppress(thread_queue.Full):
            self._queue.put_nowait(warn_msg)
```

**3c. 新增 import**（文件顶部）：

```python
import logging
```

**3d. 在 `run()` 循环中调用**（在 `await self._send_lines(lines)` 之后）：

```python
await self._send_lines(lines)
self._check_and_warn_drops()  # 新增
```

- [ ] **Step 4: 运行全部测试确认通过**

Run: `uv run pytest tests/test_log_reporter.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Ruff + Mypy 检查**

Run: `uv run ruff check src/slave/log_reporter.py tests/test_log_reporter.py && uv run mypy src/slave/log_reporter.py`
Expected: All checks passed

- [ ] **Step 6: 运行全量测试**

Run: `uv run pytest tests/ -v`
Expected: 全部 PASS（含新增约 12 个测试）

- [ ] **Step 7: Commit**

```bash
git add src/slave/log_reporter.py tests/test_log_reporter.py
git commit -m "✨ feat(log-reporter): 队列扩容 5000 + 丢弃告警（含本地 fallback）"
```

---

## 变更摘要

| 修复项 | 原行为 | 新行为 |
|--------|--------|--------|
| stderr 未捕获 | 异常/traceback 不上报 | stderr 同时拦截，级别下限 ERROR |
| 队列满静默丢弃 | `contextlib.suppress(Full)` | 线程安全计数 + 定期告警 master + 本地 logging 兜底 |
| 队列容量不足 | maxsize=1000 | maxsize=5000，溢出概率降 5x |

**已知限制（不在本次修复范围）：**
- TCP 发送 3 次重试耗尽后仍会丢弃整批日志（需要本地文件缓冲，影响面较大，单独规划）

## 验证

### 自动化
- `uv run ruff check src/slave/log_reporter.py` — 无错误
- `uv run mypy src/slave/log_reporter.py` — 无新增错误
- `uv run pytest tests/ -v` — 全部通过

### 手动端到端
1. 部署修复后的 slave 到 VM
2. 在 VM 上触发 Python 异常（stderr 输出）→ master 日志面板应显示 ERROR 级别日志
3. 模拟网络断开 30s → 恢复后 master 应收到 `[日志丢弃] 队列溢出，丢弃 N 条日志` 告警
4. 正常运行下确认无性能回退
