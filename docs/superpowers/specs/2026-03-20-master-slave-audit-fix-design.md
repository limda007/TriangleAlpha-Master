# Master-Slave 功能对接审计修复方案

> Date: 2026-03-20
> Status: Approved
> Scope: 21 issues (P0×5 + P1×8 + P2×8)

## 背景

全面审计 master-slave 对接后发现 21 个问题。TestDemo.exe（游戏脚本）不可修改，所有适配在 slave/master Python 端完成。TCP 安全增强跳过（内网环境）。

## 约束

- TestDemo.exe 不可控，STATUS 消息格式固定
- 不修改 TCP 命令协议格式（保持向后兼容）
- 修复不能破坏已有的 slave↔master 通信
- 滚动升级兼容：UDP 协议格式不变，新增 `game_state` 仅影响 master 内部逻辑，旧版 slave 发的 STATUS 消息格式不变
- `account_interface.py` 不连接 `node_updated` 信号，不受模块 A 影响（已确认）

---

## 模块 A: 信号架构重构

**解决**: #2 完成流转竞态 | #3 SQL 风暴 | #11 状态硬编码

### 根因

`NodeInfo.status` 同时承担"连接状态"（在线/离线/断连）和"游戏状态"（运行中/已完成/脚本已停止）两个语义。EXT_ONLINE 心跳每 3s 将 `status` 覆盖为"在线"，导致 STATUS 消息设置的"已完成"被冲掉。`node_updated` 信号粒度太粗，心跳也触发 `_syncAccountFromNode`。

### 改动

#### A1. `models.py` — NodeInfo 新增 `game_state` 字段

```python
@dataclass
class NodeInfo:
    # ... 现有字段 ...
    game_state: str = ""  # TestDemo 上报的游戏状态（运行中/已完成/脚本已停止）
```

`status` 仅用于连接状态（在线/离线/断连），`game_state` 存储 TestDemo 上报的原始 state。

#### A2. `protocol.py` — 定义 GameState 常量

```python
class GameState:
    """TestDemo.exe STATUS 消息的 state 字段约定值"""
    COMPLETED = "已完成"
    RUNNING = "运行中"
    SCRIPT_STOPPED = "脚本已停止"  # slave 检测到 TestDemo 停止时上报
```

#### A3. `node_manager.py` — 新增专用信号 + 修改 _handle_status

```python
class NodeManager(QObject):
    node_status_reported = pyqtSignal(str)  # 仅 STATUS 消息触发
```

`_handle_status` 改动：
- `node.game_state = msg.state`（不再写 `node.status`）
- `node.status` 保持不变（由心跳和超时管理）
- emit `node_status_reported` 而非仅 `node_updated`

#### A4. `main_window.py` — _syncAccountFromNode 只连 node_status_reported

```python
self.nodeManager.node_status_reported.connect(self._syncAccountFromNode)
# 移除: self.nodeManager.node_updated.connect(self._syncAccountFromNode)
```

`_syncAccountFromNode` 内部使用 `node.game_state` 判断：

```python
def _syncAccountFromNode(self, machine_name: str) -> None:
    node = self.nodeManager.nodes.get(machine_name)
    if not node:
        return
    self.accountPool.update_from_status(
        machine_name, node.level, node.jin_bi, node.game_state,
    )
```

#### A5. `bigscreen_interface.py` — 表格显示逻辑

"运行状态"列显示：`node.game_state if node.game_state else node.status`

---

## 模块 B: 日志接收 + 进程状态上报

**解决**: #1 日志丢失 | #5 TestDemo 崩溃状态过期 | #17 recv 无上限

### B1. `log_receiver.py` — 持久连接模式

替换 `_handle_conn` 为循环读取：

```python
def _handle_conn(self, conn):
    MAX_BUF = 1024 * 1024  # 1MB 上限
    try:
        conn.settimeout(30.0)
        buf = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_BUF:
                break  # 防 OOM
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="ignore").strip()
                if text:
                    self._parse_line(text)
    except Exception:
        pass
    finally:
        conn.close()
    # 处理末尾无换行的残余
    if buf:
        text = buf.decode("utf-8", errors="ignore").strip()
        if text:
            self._parse_line(text)
```

### B2. slave `heartbeat.py` — 新增 send_status 方法

**设计决策**: 使用独立的**阻塞式 UDP socket** 每次调用时创建并发送，而非复用心跳的非阻塞 async socket。原因：
- 心跳的 socket 在 `run()` 中以 `with` 管理、`sock.setblocking(False)` 且通过 `await loop.sock_sendto()` 使用，跨协程直接调用 `sendto()` 会抛 `BlockingIOError`
- UDP `sendto` 是近乎瞬时操作，每次创建 socket 的开销可忽略（仅在 TestDemo 状态变化时调用，不是高频操作）

```python
class HeartbeatService:
    def send_status(self, state: str, level: int = 0,
                    jin_bi: str = "0", desc: str = "",
                    elapsed: str = "0") -> None:
        """供外部模块（如 process_monitor）发送 STATUS 消息到 master

        使用独立的阻塞 UDP socket，不复用心跳的 async socket。
        """
        msg = build_udp_status(self._machine_name, state, level, jin_bi, desc, elapsed)
        data = msg.encode("utf-8")
        target = (self._master_ip, self._port) if self._master_ip else ("255.255.255.255", self._port)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(data, target)
```

### B3. slave `backend.py` — _process_monitor 增加上报

**前置改动**: 在 `_run_services` 中将 `heartbeat` 局部变量提升为实例属性 `self._heartbeat`：

```python
# backend.py _run_services() 内：
# 改动前: heartbeat = HeartbeatService(...)
# 改动后:
self._heartbeat = HeartbeatService(
    master_ip=self._master_ip,
    on_sent=self._on_heartbeat,
)
# 后续所有 heartbeat 引用改为 self._heartbeat
```

`_process_monitor` 增加状态变化检测和上报：

```python
async def _process_monitor(self) -> None:
    was_running = False
    while self._running:
        running = self._is_testdemo_running()
        self.script_status.emit(running)
        # 检测到 TestDemo 从运行→停止，上报 master
        if was_running and not running:
            try:
                self._heartbeat.send_status(GameState.SCRIPT_STOPPED)
            except Exception as e:
                print(f"[状态上报] 发送失败: {e}")
        was_running = running
        await asyncio.sleep(10)
```

### B4. master `node_manager._handle_status` — 处理"脚本已停止"

收到 `game_state == "脚本已停止"` 时清空相关字段：

```python
if msg.state == GameState.SCRIPT_STOPPED:
    node.game_state = ""
    node.current_account = ""
    node.level = 0
    node.jin_bi = "0"
    node.elapsed = "0"
```

---

## 模块 C: UI 操作修复

**解决**: #4 DELETE_FILE 失效 | #6 SENDFILE 死代码 | #7 按钮误导 | #10 SET_GROUP 缺入口 | #15 dm 误杀

### C1. DELETE_FILE 添加文件名输入 (#4)

在 `_deleteFileOnAll` 中弹窗获取文件名列表：

```python
def _deleteFileOnAll(self) -> None:
    dlg = MessageBox("批量删除文件", "输入要删除的文件名（每行一个）", self.window())
    edit = PlainTextEdit(dlg)
    edit.setPlaceholderText("accounts.txt\nkey.txt\n...")
    edit.setMinimumHeight(120)
    dlg.textLayout.addWidget(edit)
    if not dlg.exec():
        return
    filenames = [l.strip() for l in edit.toPlainText().splitlines() if l.strip()]
    if not filenames:
        return
    payload = "|".join(filenames)
    ips, selected = self._getTargetIPs()
    # ... 确认 + 发送 ...
    self._tcp.broadcast(ips, TcpCommand.DELETE_FILE, payload)
```

同时修改 `build_tcp_command` 对 DELETE_FILE 的处理，确保 payload 直接拼接：

```python
elif cmd in (TcpCommand.EXT_SET_GROUP, TcpCommand.DELETE_FILE) and payload:
    return f"{cmd.value}|{payload}"
```

### C2. 清理 SENDFILE 死代码 (#6)

- 从 `protocol.py` 移除 `SENDFILE_START/CHUNK/END` 枚举值
- 从 `command_handler.py` 移除 `_handle_sendfile`、`_read_chunks` 方法和 `MAX_FILE_SIZE` 常量
- 移除 `_dispatch` 中的 SENDFILE 分支
- 移除 `command_handler.py` 顶部的 `import tempfile`（仅 SENDFILE 使用）
- 添加注释说明文件下发使用 UPDATE_TXT 通道
- **测试文件清理**:
  - `tests/test_e2e_fixes.py`: 删除 `TestSendFileE2E` 类（SENDFILE roundtrip/traversal 测试）
  - `tests/test_slave_fixes.py`: 从 `test_remaining_commands` 期望集合中移除 `SENDFILE_*`；删除 `test_max_file_size_in_sendfile_handler`
  - `tests/test_e2e_fixes.py`: 删除 `_handle_sendfile` 源码检查断言

### C3. 按钮改名 (#7)

- "一键下发文件" → "下发账号文件"
- `_sendFileToAll` 文件选择器过滤器限制为 `"Text (*.txt)"`
- 添加注释：`# 通过 UPDATE_TXT 覆盖 slave 端 accounts.txt`

### C4. 添加"设置分组"右键菜单 (#10)

在 `_showNodeContextMenu` 的在线节点菜单中添加：

```python
menu.addAction(
    Action(FIF.TAG, "设置分组",
           triggered=lambda: self._setNodeGroup(ip, machine_name))
)
```

实现 `_setNodeGroup` 弹窗输入分组名后发送 `EXT_SET_GROUP`。

### C5. 修正 dm 进程名 (#15)

```python
_KILL_TARGETS = [
    # ... 其他不变 ...
    # "dm",  # 移除：太宽泛，可能误杀
]
_KILL_KEYWORDS = [
    "rapidocr",
    "dmsoft",  # 大漠插件相关进程
]
```

---

## 模块 D: 技术债清理

### D1. `account_db.py` allocate 加排序 (#8)

修改 `allocate()` 方法中的子查询，添加 `ORDER BY id` 保证按导入顺序分配：

```sql
UPDATE accounts SET status='运行中', assigned_machine=?
WHERE id = (SELECT id FROM accounts WHERE status='空闲中' ORDER BY id LIMIT 1)
RETURNING *
```

### D2. 删除 `account_pool.py` (#12)

- 删除 `src/master/app/core/account_pool.py`
- 将引用 AccountPool 的测试迁移到 AccountDB
- 更新 import

### D3. SENDFILE 全局超时 (#13)

随 C2 一起移除，不再需要。

### D4. `backend.stop()` 异常处理 (#18)

> 注意：检查当前工作树中 `backend.py` 是否已包含此 try/except（git status 显示该文件已修改）。若已修改则跳过。

```python
def stop(self) -> None:
    self._running = False
    if self._loop and self._loop.is_running():
        try:
            self._loop.call_soon_threadsafe(self._request_shutdown)
        except RuntimeError:
            pass  # loop 已关闭
```

### D5. `main.py` wait 超时改 log (#19)

```python
if not backend.wait(5000):
    print("[警告] SlaveBackend 未在 5 秒内停止")
    # 不再 raise，避免覆盖原始异常
```

### D6. `_sendFileToAll` 读取方式 (#16)

随 C3 一起修复，限制为 .txt 文件，保持 UTF-8 文本模式。

### D7. 死代码标注 (#14)

`build_udp_online()` 添加 `# legacy: 仅测试使用，生产环境由 EXT_ONLINE 替代` 注释。

> 注意：`build_udp_status()` 在 B2 的 `send_status` 方法中被生产代码使用，**不是死代码**，不添加 legacy 标注。

### D8. 超时监控分级（原 P1）

```python
def _checkStaleNodes(self) -> None:
    threshold_min = self.spinTimeout.value()
    now = datetime.now()
    for node in self._nm.nodes.values():
        if node.status in ("离线", "断连"):
            continue
        if not node.game_state:  # 未启动脚本的节点跳过
            continue
        elapsed = (now - node.last_status_update).total_seconds() / 60
        if elapsed >= threshold_min:
            # 先软重启脚本，而非直接重启电脑
            self._tcp.send(node.ip, TcpCommand.STOP_EXE)
            self._tcp.send(node.ip, TcpCommand.START_EXE)
```

### D9. pyqtSignal(object) 注释 (#20)

在 `udp_listener.py` 和 `log_receiver.py` 的信号定义处添加注释：

```python
# 跨线程传递 Python 对象：emit 后不得修改对象内容
message_received = pyqtSignal(object, str)
```

### D10. AccountDB GUI 线程 TODO (#21)

```python
class AccountDB(QObject):
    """SQLite 持久化账号池

    TODO: 大规模部署（>5000 账号 + >100 节点）时应将 DB 操作移到工作线程
    """
```

---

## 文件影响矩阵

| 文件 | 模块 | 改动类型 |
|------|------|----------|
| `src/common/models.py` | A1 | 新增 game_state 字段 |
| `src/common/protocol.py` | A2, C2 | 新增 GameState 常量；删除 SENDFILE 枚举；修改 build_tcp_command |
| `src/master/app/core/node_manager.py` | A3, B4 | 新增信号；修改 _handle_status |
| `src/master/app/view/main_window.py` | A4 | 改连信号；修改 _syncAccountFromNode |
| `src/master/app/view/bigscreen_interface.py` | A5, C1-C4, D8 | 表格显示；按钮改名；DELETE 弹窗；SET_GROUP 菜单；超时分级 |
| `src/master/app/core/log_receiver.py` | B1 | 持久连接模式 |
| `src/master/app/core/account_db.py` | D1 | ORDER BY |
| `src/master/app/core/account_pool.py` | D2 | 删除 |
| `src/slave/heartbeat.py` | B2 | 新增 send_status |
| `src/slave/backend.py` | B3, D4 | _process_monitor 上报；stop 异常处理 |
| `src/slave/command_handler.py` | C2 | 删除 SENDFILE 处理 |
| `src/slave/process_manager.py` | C5 | 修正 dm 进程名 |
| `src/slave/main.py` | D5 | wait 超时改 log |
| `src/master/app/core/udp_listener.py` | D9 | 注释 |
| `tests/test_e2e_fixes.py` | C2 | 删除 SENDFILE 测试类和断言 |
| `tests/test_slave_fixes.py` | C2 | 移除 SENDFILE 枚举断言和超大文件测试 |
| `tests/*` (AccountPool 引用) | D2 | 迁移 AccountPool 测试到 AccountDB |

## 测试计划

### 模块 A 测试
- 单元测试 `NodeInfo.game_state` 字段默认值为空字符串
- 单元测试 `_handle_status` 写入 `game_state` 而非覆盖 `status`
- 集成测试：EXT_ONLINE 后接 STATUS("已完成") → 验证 `game_state=="已完成"` 且 `status=="在线"`
- 集成测试：`node_status_reported` 信号仅在 STATUS 消息时触发（EXT_ONLINE 不触发）

### 模块 B 测试
- 单元测试 `HeartbeatService.send_status()` 发送正确格式的 UDP STATUS 消息
- 单元测试 `_process_monitor` 检测到 was_running→not_running 时调用 `send_status`
- 集成测试 `log_receiver` 持久连接：同一 TCP 连接发送多行日志，验证全部接收

## 实施顺序

1. 模块 A（信号架构）— 基础改动，其他模块依赖
2. 模块 B（通信层）— 依赖 A 的 game_state 字段
3. 模块 C（UI 操作）— 可独立
4. 模块 D（技术债）— 可独立
