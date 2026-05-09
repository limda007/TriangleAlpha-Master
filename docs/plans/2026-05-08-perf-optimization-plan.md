# Performance Optimization Plan — TriangleAlpha

**Date:** 2026-05-08
**Status:** APPROVED (consensus reached after 2 iterations)
**Scope:** build-in-mutagen 构建性能 + Master/Slave 运行时性能

---

## RALPLAN-DR Summary

### Principles (5)

1. **最小改动最大收益** — 优先落地 ≤10 行改动但性能提升 >30% 的项目
2. **向后兼容不可破** — 协议/API/DB 格式不变，现有 agent (slave/astar_agent/TestDemo) 无感知
3. **可度量验证** — 每项改动有明确的用户可感知度量指标
4. **构建与运行分离** — 构建加速不影响运行时正确性，运行时优化不增加构建复杂度
5. **渐进式交付** — 分 3 个 Phase 独立可合，每 Phase 自包含、有明确回滚路径

### Decision Drivers (Top 3)

1. **构建等待时间** — 当前 `build-in-mutagen` 完整周期约 60-120s，目标是降到 40-60s
2. **100+ 节点下的 UI 响应性** — 当前主线程 SQLite GROUP BY + 频繁 `_refresh_counts` 在大规模时卡顿
3. **内存占用** — 每个 NodeInfo/UdpMessage 对象无 `__slots__`，100+ 节点时浪费 ~200KB+ dict overhead

### Viable Options (3)

| Option | 描述 | 优点 | 缺点 |
|--------|------|------|------|
| **A: 仅 Spec 优化** | 只改 spec 文件 (strip/upx/excludes) | 零代码风险 | 收益有限 (~30% 构建加速) |
| **B (Revised): Spec + Makefile + DB 缓存 + __slots__** | A + uv sync 跳过 + _refresh_counts 缓存 + __slots__ | 高收益低风险 | 需改动 5 个文件 |
| **C: 全量重构** | B + IP 索引 + DB 线程 + TCP 连接池 + orjson | 最大收益 | 风险高，DB 线程/IP 索引改动影响面大 |

**推荐 Option B (Revised)** — 按 Architect 建议将 IP 索引移至 Phase 3，将 AccountDB 缓存提前到 Phase 2 解决真正瓶颈。

---

## Phase 1: Build 构建加速 (Spec + Makefile)

### 1.1 开启 PyInstaller strip + UPX

**文件:** `master.spec:128,147`, `slave.spec:110,138`

```python
# Before
debug=False, strip=False, upx=False,

# After
debug=False, strip=True, upx=True,
```

**前置验证:** `ssh $(MUTAGEN_HOST) "where upx 2>nul || echo UPX_NOT_FOUND"`
若 UPX 不存在则仅开启 strip，UPX 保持 False。

**度量:** 二进制体积缩减 40-60%，启动时间减少 15-25%
**风险:** UPX 在极少数杀软上可能误报，需验证
**回滚:** 单文件 revert strip=False, upx=False

### 1.2 uv sync 条件化

**文件:** `Makefile` (新增 `_mutagen-uv-hash` 目标)

避免 Windows `cmd /c` 没有 `sha256sum` 且不同平台 `stat`/`%~tf` 时间格式不兼容的问题，只比较文件大小（lock 文件大小对依赖变更检测足够）：

```makefile
_UVLOCK_SIZE := $(shell wc -c < uv.lock 2>/dev/null || echo 0)

_mutagen-uv-sync:
	@REMOTE_SIZE=$$(ssh $(MUTAGEN_HOST) "cmd /c \"for %f in ($(MUTAGEN_REPO)\\uv.lock) do @echo %~zf\"" 2>/dev/null); \
	if [ "$$REMOTE_SIZE" != "$(_UVLOCK_SIZE)" ]; then \
		echo ">>> 依赖已变更，重新同步..."; \
		ssh $(MUTAGEN_HOST) "cmd /c set UV_LINK_MODE=copy&& cd /d $(MUTAGEN_REPO) && $(MUTAGEN_UV) sync --group dev --default-index $(MUTAGEN_PYPI_INDEX)"; \
	else \
		echo ">>> 依赖未变更，跳过 uv sync"; \
	fi
```

**度量:** 跳过 uv sync 节省 5-15s
**回滚:** 恢复无条件 `uv sync` 行

### 1.3 增加 build-in-mutagen-fast 目标

**文件:** `Makefile` (新增目标)

```makefile
build-in-mutagen-fast: ## 增量构建（跳过 --clean，更快但可能残留旧缓存）
	@echo ">>> 刷新 Mutagen 同步..."
	@if ! mutagen project flush >/dev/null 2>&1; then \
		mutagen project start; \
		mutagen project flush; \
	fi
	@$(MAKE) _mutagen-uv-sync
	@echo ">>> 增量构建 Master..."
	ssh $(MUTAGEN_HOST) "cmd /c set UV_LINK_MODE=copy&& cd /d $(MUTAGEN_REPO) && $(MUTAGEN_UV) run pyinstaller --noconfirm $(MASTER_SPEC)"
```

**度量:** 增量构建节省 10-20s（跳过 --clean 的缓存清除）
**回滚:** 删除该目标即可

### 1.4 excludes 增强（Master + Slave）

**文件:** `master.spec:113`, `slave.spec:106`

Master:
```python
excludes=['tkinter', 'matplotlib', 'numpy', 'tornado', 'sqlalchemy', 'flask'],
```

Slave:
```python
excludes=['tkinter', 'matplotlib', 'qfluentwidgets', 'tornado', 'sqlalchemy', 'flask'],
```

> 验证了 `tornado`/`sqlalchemy`/`flask` 非项目依赖，不会因传递依赖引入。

## Phase 2: 运行时响应性与内存优化

### 2.1 AccountDB 统计增量缓存 (_refresh_counts 去 GROUP BY)

**文件:** `src/master/app/core/account_db.py`

**现状:** 每次 `allocate`/`release`/`complete`/`import_fresh` 后调用 `_refresh_counts()`，执行 `SELECT status, COUNT(*) FROM accounts GROUP BY status`。5000+ 账号时全表扫描耗时 1-5ms，这不是很大但频繁调用会累积。

**改动:** 在写操作中直接维护 `_total`/`_available`/`_in_use`/`_completed` 计数，无需 GROUP BY：

```python
def _increment_count(self, status: str) -> None:
    self._total += 1
    if status == "空闲中": self._available += 1
    elif status == "运行中": self._in_use += 1
    elif status == "已完成": self._completed += 1

def _decrement_count(self, status: str) -> None:
    self._total = max(0, self._total - 1)
    if status == "空闲中": self._available = max(0, self._available - 1)
    elif status == "运行中": self._in_use = max(0, self._in_use - 1)
    elif status == "已完成": self._completed = max(0, self._completed - 1)

def _transition_count(self, old_status: str, new_status: str) -> None:
    self._decrement_count(old_status)
    self._increment_count(new_status)
```

在 `allocate`/`release`/`complete`/`upsert_from_sync` 等方法中调用 `_transition_count` 替代全量 `_refresh_counts`。保留 `_refresh_counts` 仅用于 `import_fresh`/`clear_all` 等批量操作。

**度量:** `allocate`/`release` 耗时从 1-5ms → <0.1ms (SQL GROUP BY 消除)
**回滚:** 恢复每处调用 `_refresh_counts()`

### 2.2 UdpMessage / NodeInfo / AccountInfo 添加 __slots__

**文件:** `src/common/protocol.py:86`, `src/common/models.py:21,56`

Python 3.12 支持 `@dataclass(slots=True)` 与 `field(default_factory=datetime.now)` 兼容。

变更前验证：grep `.__dict__` 和动态属性访问确认无外部依赖 dataclass 的 dict。

```python
# protocol.py
@dataclass(slots=True)
class UdpMessage:
    ...

# models.py
@dataclass(slots=True)
class NodeInfo:
    ...

@dataclass(slots=True)
class AccountInfo:
    ...
```

**度量:** 单实例内存减少 ~40%（__dict__ → __slots__），100 节点节省 ~80KB+
**风险:** 动态属性赋值会抛 AttributeError；需 grep 确认项目中无 `node.xxx = yyy` 动态赋值
**回滚:** 删除 `slots=True` 恢复原 dataclass

### 2.3 NodeManager 批量更新 IP 变更检测

**文件:** `src/master/app/core/node_manager.py:_handle_status`

当前 `_handle_status` 对 STATUS 消息做 `node.xxx = yyy` 逐个属性赋值，但未更新 IP。增加 IP 变更检测（不影响 `_handle_status` 的 partial-update 语义）：

```python
# _handle_status 中，node 已存在时：
if node.ip != remote_ip:
    node.ip = remote_ip
```

**度量:** STATUS 消息中的 IP 变更能被正确追踪（修复现有小 bug，非性能优化）

## Phase 3: 深度优化（待 Phase 1+2 上线稳定后评估）

### 3.1 NodeManager IP 反向索引
- 新增 `_ip_index: dict[str, str]` 在 `_upsert_node` + `_handle_status` + `purge_stale_nodes` 三处维护
- `get_node_by_ip` O(n) → O(1)
- **风险:** 需保证三处同步，多线程访问需加锁
- **当前收益低:** 5μs 操作不被用户感知；仅当 TCP 发送频率达到每秒数百条时才有意义

### 3.2 TCP 连接池
### 3.3 json → orjson 替换
### 3.4 AccountDB 工作线程化

---

## Verification Plan

### Per-Phase Verification

| Phase | 验证方法 | 通过标准 |
|-------|---------|---------|
| 1.1 | 构建后对比 exe 体积 + `time` 启动 | 体积缩减 >30%，启动 <1s |
| 1.1 | `ssh $(MUTAGEN_HOST) "where upx"` 前置检查 | 确认 UPX 可用，否则降级 strip-only |
| 1.2 | 连续两次 build-in-mutagen，观察 uv sync 日志 | 第二次输出 "依赖未变更，跳过 uv sync" |
| 1.3 | `make build-in-mutagen-fast` 后对比全量构建时间 | 快 10-20s |
| 1.4 | 构建后 `import tornado` 确认 excluded | ImportError |
| 2.1 | 导入 5000 账号后 100 次 alloc/release 计时 | 每次 <0.5ms（vs 当前 1-5ms） |
| 2.1 | `make check` 回归 | lint + typecheck + test 全绿 |
| 2.2 | `sys.getsizeof(NodeInfo(...))` 对比 | slots 版减少 >100 bytes |
| 2.2 | grep `\.__dict__` 和动态属性确认 | 无依赖 dataclass __dict__ 的代码 |
| 2.3 | STATUS 消息变更 IP 后检查 node.ip | IP 正确更新 |

### 回归检查

```bash
make check  # ruff check + mypy + pytest
```

### 回滚验证

每个 Phase 独立 revert 后 `make check` 仍通过。

---

## ADR (Architecture Decision Record)

- **Decision:** Option B Revised — Spec 优化 + uv sync 条件化 + AccountDB 增量计数 + __slots__
- **Drivers:** 构建等待时间、100+ 节点 UI 响应性、内存占用
- **Alternatives considered:**
  - Option A (仅 Spec): 被拒绝 — 未解决运行时瓶颈
  - IP 索引 (原 Phase 2.1): 移至 Phase 3 — 5μs 操作非用户可感知瓶颈，且三处同步维护引入正确性风险
  - Option C (全量重构): 被拒绝 — Phase 3 待验证
- **Why chosen:** Phase 1+2 覆盖 80% 用户可感知收益，每项 ≤20 行改动，独立可回滚
- **Consequences:**
  - UPX 需 Windows VM 上有 `upx.exe` 在 PATH
  - `__slots__` 禁止动态属性赋值，需 grep 确认无现有依赖
  - uv sync 条件化的 stat 方案在文件系统精度足够时可靠
- **Follow-ups:** Phase 3 在 Phase 1+2 上线且稳定后评估 IP 索引和 DB 线程化

---

## 审阅记录

| Round | Reviewer | Verdict | Key Feedback |
|-------|----------|---------|-------------|
| 1 | Architect | ITERATE | sha256sum 在 Windows 不存在；_handle_status 绕过了 _upsert_node 导致 IP 索引反同步；建议将 DB 优化提前、IP 索引押后 |
| 1 | Critic | ITERATE | 确认 Architect 的 2 个 CRITICAL 发现；强调 IP 索引优先级倒置；要求增加回滚路径和线程安全说明 |
| 2 | Architect | APPROVE | 所有 7 项 Round 1 问题已解决；建议 Phase 2.1 增量计数器注明线程安全假设 |
| 2 | Critic | ACCEPT-WITH-RESERVATIONS | stat 格式不匹配修复后即可执行；确认线程安全在当前 Qt 主线程模型下无回归；建议 upsert_from_sync 保留 _refresh_counts |
