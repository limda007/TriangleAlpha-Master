# Master-Slave 审计修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 21 issues from the master-slave audit: 5×P0 + 8×P1 + 8×P2

**Architecture:** Decouple game state from connection state in NodeInfo, fix log receiver persistent connections, add TestDemo crash detection reporting, fix broken UI commands, clean up dead code.

**Tech Stack:** Python 3.14, PyQt6, asyncio, SQLite, UDP/TCP sockets

**Spec:** `docs/superpowers/specs/2026-03-20-master-slave-audit-fix-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/common/models.py` | Shared data models | Add `game_state` field to NodeInfo |
| `src/common/protocol.py` | Protocol constants & builders | Add GameState class; remove SENDFILE enums; fix build_tcp_command |
| `src/master/app/core/node_manager.py` | Node state machine | Add `node_status_reported` signal; rewrite `_handle_status` |
| `src/master/app/view/main_window.py` | Main window wiring | Rewire signal; fix `_syncAccountFromNode` |
| `src/master/app/view/bigscreen_interface.py` | Big screen UI | Display game_state; fix buttons; add SET_GROUP menu |
| `src/master/app/core/log_receiver.py` | TCP log receiver | Persistent connection support |
| `src/master/app/core/account_db.py` | SQLite account pool | Fix allocate ORDER BY |
| `src/master/app/core/account_pool.py` | Legacy account pool | DELETE |
| `src/slave/heartbeat.py` | UDP heartbeat | Add `send_status()` method |
| `src/slave/backend.py` | Slave backend orchestrator | Promote heartbeat to instance attr; enhance process monitor |
| `src/slave/command_handler.py` | TCP command handler | Remove SENDFILE code |
| `src/slave/process_manager.py` | Process management | Fix dm kill target |
| `src/slave/main.py` | Slave entry point | Fix wait timeout |
| `src/master/app/core/udp_listener.py` | UDP listener | Add signal safety comment |
| `tests/test_node_manager.py` | Node manager tests | Add game_state tests |
| `tests/test_slave_fixes.py` | Slave fix tests | Remove SENDFILE assertions |
| `tests/test_e2e_fixes.py` | E2E tests | Remove SENDFILE tests |
| `tests/test_integration.py` | Integration tests | Migrate from AccountPool to AccountDB |
| `tests/test_account_pool.py` | AccountPool tests | DELETE (replaced by test_account_db.py) |
| `tests/test_master_fixes.py` | Master fix tests | Migrate AccountPool refs |

---

### Task 1: A1+A2 — Add `game_state` field and `GameState` constants

**Files:**
- Modify: `src/common/models.py:17` (NodeInfo dataclass)
- Modify: `src/common/protocol.py:17` (after UdpMessageType)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write test for game_state field**

In `tests/test_models.py`, add:

```python
class TestNodeInfoGameState:
    def test_game_state_default_empty(self):
        from common.models import NodeInfo
        node = NodeInfo(machine_name="VM-01", ip="1.2.3.4")
        assert node.game_state == ""

    def test_game_state_assignable(self):
        from common.models import NodeInfo
        node = NodeInfo(machine_name="VM-01", ip="1.2.3.4")
        node.game_state = "已完成"
        assert node.game_state == "已完成"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::TestNodeInfoGameState -v`
Expected: FAIL — `NodeInfo.__init__() got an unexpected keyword argument 'game_state'` or `AttributeError`

- [ ] **Step 3: Add game_state to NodeInfo**

In `src/common/models.py`, add after `last_status_update` field (line 31):

```python
    game_state: str = ""  # TestDemo 上报的游戏状态（运行中/已完成/脚本已停止）
```

- [ ] **Step 4: Add GameState constants to protocol.py**

In `src/common/protocol.py`, add after `UdpMessageType` class (after line 21):

```python
class GameState:
    """TestDemo.exe STATUS 消息的 state 字段约定值"""
    COMPLETED = "已完成"
    RUNNING = "运行中"
    SCRIPT_STOPPED = "脚本已停止"  # slave 检测到 TestDemo 停止时上报
```

- [ ] **Step 5: Write test for GameState constants**

In `tests/test_protocol.py`, add:

```python
class TestGameStateConstants:
    def test_completed_value(self):
        from common.protocol import GameState
        assert GameState.COMPLETED == "已完成"

    def test_script_stopped_value(self):
        from common.protocol import GameState
        assert GameState.SCRIPT_STOPPED == "脚本已停止"
```

- [ ] **Step 6: Run all tests**

Run: `uv run pytest tests/test_models.py::TestNodeInfoGameState tests/test_protocol.py::TestGameStateConstants -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/common/models.py src/common/protocol.py tests/test_models.py tests/test_protocol.py
git commit -m "$(cat <<'EOF'
✨ feat(common): 新增 game_state 字段和 GameState 常量

解耦连接状态（status）和游戏状态（game_state），
为修复完成流转竞态和 SQL 风暴做准备。
EOF
)"
```

---

### Task 2: A3 — NodeManager 新增 node_status_reported 信号 + 重写 _handle_status

**Files:**
- Modify: `src/master/app/core/node_manager.py:14-198`
- Test: `tests/test_node_manager.py`

- [ ] **Step 1: Write tests for new signal and game_state behavior**

In `tests/test_node_manager.py`, add:

```python
class TestHandleStatusGameState:
    """验证 _handle_status 写入 game_state 而非覆盖 status"""

    def test_status_writes_game_state_not_status(self):
        """STATUS 消息应写入 game_state，不覆盖 status"""
        from common.protocol import GameState, UdpMessage, UdpMessageType
        from master.app.core.node_manager import NodeManager

        nm = NodeManager()
        # 先通过 EXT_ONLINE 建立节点
        ext_msg = UdpMessage(type=UdpMessageType.EXT_ONLINE, machine_name="VM-01",
                             user_name="user1", cpu_percent=10.0, mem_percent=20.0,
                             slave_version="2.0.0", group="默认")
        nm.handle_udp_message(ext_msg, "10.0.0.1")
        assert nm.nodes["VM-01"].status == "在线"

        # 发送 STATUS 消息
        status_msg = UdpMessage(type=UdpMessageType.STATUS, machine_name="VM-01",
                                state="已完成", level=18, jin_bi="12450",
                                desc="account1", elapsed="360")
        nm.handle_udp_message(status_msg, "10.0.0.1")

        node = nm.nodes["VM-01"]
        assert node.game_state == "已完成", "game_state 应为 STATUS 消息的 state"
        assert node.status == "在线", "status 不应被 STATUS 消息覆盖"
        assert node.level == 18
        assert node.jin_bi == "12450"
        assert node.current_account == "account1"
        assert node.elapsed == "360"

    def test_ext_online_does_not_overwrite_game_state(self):
        """EXT_ONLINE 心跳不应覆盖 game_state"""
        from common.protocol import UdpMessage, UdpMessageType
        from master.app.core.node_manager import NodeManager

        nm = NodeManager()
        # STATUS 先到
        status_msg = UdpMessage(type=UdpMessageType.STATUS, machine_name="VM-01",
                                state="运行中", level=5, jin_bi="1000",
                                desc="acc1", elapsed="60")
        nm.handle_udp_message(status_msg, "10.0.0.1")
        assert nm.nodes["VM-01"].game_state == "运行中"

        # EXT_ONLINE 后到
        ext_msg = UdpMessage(type=UdpMessageType.EXT_ONLINE, machine_name="VM-01",
                             user_name="user1", cpu_percent=50.0, mem_percent=60.0,
                             slave_version="2.0.0", group="默认")
        nm.handle_udp_message(ext_msg, "10.0.0.1")
        assert nm.nodes["VM-01"].game_state == "运行中", "EXT_ONLINE 不应清空 game_state"
        assert nm.nodes["VM-01"].status == "在线"

    def test_node_status_reported_signal_only_on_status(self):
        """node_status_reported 仅在 STATUS 消息时触发"""
        from common.protocol import UdpMessage, UdpMessageType
        from master.app.core.node_manager import NodeManager

        nm = NodeManager()
        reported = []
        nm.node_status_reported.connect(reported.append)

        ext_msg = UdpMessage(type=UdpMessageType.EXT_ONLINE, machine_name="VM-01",
                             user_name="u", cpu_percent=0, mem_percent=0,
                             slave_version="2.0.0", group="默认")
        nm.handle_udp_message(ext_msg, "10.0.0.1")
        assert len(reported) == 0, "EXT_ONLINE 不应触发 node_status_reported"

        status_msg = UdpMessage(type=UdpMessageType.STATUS, machine_name="VM-01",
                                state="运行中", level=1, jin_bi="0", desc="a", elapsed="0")
        nm.handle_udp_message(status_msg, "10.0.0.1")
        assert reported == ["VM-01"], "STATUS 应触发 node_status_reported"

    def test_script_stopped_clears_fields(self):
        """脚本已停止 应清空 game_state 和相关字段"""
        from common.protocol import GameState, UdpMessage, UdpMessageType
        from master.app.core.node_manager import NodeManager

        nm = NodeManager()
        # 先有运行状态
        status_msg = UdpMessage(type=UdpMessageType.STATUS, machine_name="VM-01",
                                state="运行中", level=10, jin_bi="5000",
                                desc="acc1", elapsed="120")
        nm.handle_udp_message(status_msg, "10.0.0.1")

        # 脚本停止
        stop_msg = UdpMessage(type=UdpMessageType.STATUS, machine_name="VM-01",
                              state=GameState.SCRIPT_STOPPED, level=0, jin_bi="0",
                              desc="", elapsed="0")
        nm.handle_udp_message(stop_msg, "10.0.0.1")

        node = nm.nodes["VM-01"]
        assert node.game_state == ""
        assert node.current_account == ""
        assert node.level == 0
        assert node.jin_bi == "0"
        assert node.elapsed == "0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_node_manager.py::TestHandleStatusGameState -v`
Expected: FAIL — `node_status_reported` signal not found

- [ ] **Step 3: Implement NodeManager changes**

In `src/master/app/core/node_manager.py`:

1. Add import: `from common.protocol import GameState` (after existing protocol imports)
2. Add signal after `history_changed` (line 22):
   ```python
   node_status_reported = pyqtSignal(str)  # machine_name — 仅 STATUS 消息触发
   ```
3. Replace `_handle_status` method (lines 180-198) with:
   ```python
   def _handle_status(self, msg: UdpMessage, remote_ip: str) -> None:
       name = msg.machine_name
       if name not in self.nodes:
           self.nodes[name] = NodeInfo(machine_name=name, ip=remote_ip)
           self.node_online.emit(name)
       node = self.nodes[name]
       # 写入 game_state 而非 status（status 由心跳和超时管理）
       if msg.state == GameState.SCRIPT_STOPPED:
           node.game_state = ""
           node.current_account = ""
           node.level = 0
           node.jin_bi = "0"
           node.elapsed = "0"
       else:
           node.game_state = msg.state if msg.state else node.game_state
           node.level = msg.level
           node.jin_bi = msg.jin_bi
           node.elapsed = msg.elapsed
           if msg.desc:
               node.current_account = msg.desc
       node.last_seen = datetime.now()
       node.last_status_update = datetime.now()
       self.node_updated.emit(name)
       self.node_status_reported.emit(name)
       self._recalc_online()
       self._schedule_stats()
   ```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_node_manager.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/master/app/core/node_manager.py tests/test_node_manager.py
git commit -m "$(cat <<'EOF'
✨ feat(master): 新增 node_status_reported 信号，game_state 解耦

_handle_status 写入 game_state 而非覆盖 status，
新增 node_status_reported 信号仅在 STATUS 消息时触发。
修复 #2 完成流转竞态、#3 SQL 风暴的根因。
EOF
)"
```

---

### Task 3: A4+A5 — main_window 信号重连 + bigscreen 显示逻辑

**Files:**
- Modify: `src/master/app/view/main_window.py:70-140`
- Modify: `src/master/app/view/bigscreen_interface.py:397-434`

- [ ] **Step 1: Rewire main_window signal**

In `src/master/app/view/main_window.py`:

1. Change line 71 from:
   ```python
   self.nodeManager.node_updated.connect(self._syncAccountFromNode)
   ```
   to:
   ```python
   self.nodeManager.node_status_reported.connect(self._syncAccountFromNode)
   ```

2. Change `_syncAccountFromNode` (lines 133-140) to use `game_state`:
   ```python
   def _syncAccountFromNode(self, machine_name: str) -> None:
       """slave STATUS 上报 → 同步等级/金币/状态到 AccountDB"""
       node = self.nodeManager.nodes.get(machine_name)
       if not node:
           return
       self.accountPool.update_from_status(
           machine_name, node.level, node.jin_bi, node.game_state,
       )
   ```

- [ ] **Step 2: Update bigscreen table display**

In `src/master/app/view/bigscreen_interface.py`, change `_setRowData` (line 406):

From:
```python
node.elapsed if node.elapsed != "0" else "--",
node.status,
```
To:
```python
node.elapsed if node.elapsed != "0" else "--",
node.game_state if node.game_state else node.status,
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `uv run pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/master/app/view/main_window.py src/master/app/view/bigscreen_interface.py
git commit -m "$(cat <<'EOF'
🐛 fix(master): _syncAccountFromNode 只响应 STATUS 信号

改连 node_status_reported 信号，使用 game_state 判断完成流转。
大屏表格显示 game_state 优先于 status。
修复 #2 完成流转竞态和 #3 SQL 风暴。
EOF
)"
```

---

### Task 4: B1 — log_receiver 持久连接支持

**Files:**
- Modify: `src/master/app/core/log_receiver.py:67-86`
- Test: `tests/test_e2e_fixes.py`

- [ ] **Step 1: Write test for multi-line persistent connection**

In `tests/test_e2e_fixes.py`, add to `TestLogMessageFormat`:

```python
def test_persistent_connection_multi_line(self):
    """同一 TCP 连接发送多行日志应全部接收"""
    import socket
    import time
    from master.app.core.log_receiver import LogReceiverThread

    port = _free_port()
    receiver = LogReceiverThread(port=port)
    entries = []
    receiver.log_received.connect(entries.append)
    receiver.start()
    time.sleep(0.3)

    # 同一连接发送 3 行日志
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    sock.sendall(
        b"LOG|VM-01|10:00:01|INFO|line1\n"
        b"LOG|VM-01|10:00:02|INFO|line2\n"
        b"LOG|VM-01|10:00:03|INFO|line3\n"
    )
    sock.close()
    time.sleep(0.5)

    receiver.stop()
    assert len(entries) >= 3, f"应收到 3 条日志，实际收到 {len(entries)}"
    contents = [e.content for e in entries[:3]]
    assert contents == ["line1", "line2", "line3"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_e2e_fixes.py::TestLogMessageFormat::test_persistent_connection_multi_line -v`
Expected: FAIL — only 1 entry received

- [ ] **Step 3: Implement persistent connection in log_receiver**

Replace `_handle_conn` in `src/master/app/core/log_receiver.py:67-85` with:

```python
def _handle_conn(self, conn: socket.socket) -> None:
    """在线程池中处理单个客户端连接（支持持久连接多行日志）"""
    MAX_BUF = 1024 * 1024  # 1MB 缓冲区上限，防 OOM
    try:
        conn.settimeout(30.0)
        buf = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_BUF:
                break
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

Also add safety comment to the signal definition (line 31):
```python
# 跨线程传递 Python 对象：emit 后不得修改对象内容
log_received = pyqtSignal(object)  # LogEntry
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_e2e_fixes.py::TestLogMessageFormat -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/master/app/core/log_receiver.py tests/test_e2e_fixes.py
git commit -m "$(cat <<'EOF'
🐛 fix(master): log_receiver 支持持久连接多行日志

替换首行即断开逻辑为循环读取，增加 1MB 缓冲区上限。
修复 #1 日志丢失。
EOF
)"
```

---

### Task 5: B2+B3 — slave heartbeat.send_status + process_monitor 上报

**Files:**
- Modify: `src/slave/heartbeat.py`
- Modify: `src/slave/backend.py`
- Test: `tests/test_slave_fixes.py`

- [ ] **Step 1: Write test for send_status**

In `tests/test_slave_fixes.py`, add:

```python
class TestHeartbeatSendStatus:
    def test_send_status_sends_udp(self):
        """send_status 应发送正确格式的 STATUS UDP 消息"""
        from unittest.mock import patch, MagicMock
        from slave.heartbeat import HeartbeatService

        hb = HeartbeatService(master_ip="10.0.0.1")
        with patch("slave.heartbeat.socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock_cls.return_value.__exit__ = MagicMock(return_value=False)
            hb.send_status("脚本已停止")

        mock_sock.sendto.assert_called_once()
        data, target = mock_sock.sendto.call_args[0]
        msg = data.decode("utf-8")
        assert msg.startswith("STATUS|")
        assert "脚本已停止" in msg
        assert target == ("10.0.0.1", 8888)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_slave_fixes.py::TestHeartbeatSendStatus -v`
Expected: FAIL — `send_status` not found

- [ ] **Step 3: Implement send_status in heartbeat.py**

In `src/slave/heartbeat.py`, add `build_udp_status` to imports (line 12):
```python
from common.protocol import HEARTBEAT_INTERVAL, UDP_PORT, build_udp_ext_online, build_udp_offline, build_udp_status
```

Add method to `HeartbeatService` class (after `set_group`):

```python
def send_status(self, state: str, level: int = 0,
                jin_bi: str = "0", desc: str = "",
                elapsed: str = "0") -> None:
    """发送 STATUS 消息到 master（独立阻塞 UDP socket，不复用心跳 async socket）"""
    msg = build_udp_status(self._machine_name, state, level, jin_bi, desc, elapsed)
    data = msg.encode("utf-8")
    target = (self._master_ip, self._port) if self._master_ip else ("255.255.255.255", self._port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(data, target)
```

- [ ] **Step 4: Promote heartbeat to self._heartbeat in backend.py**

In `src/slave/backend.py`:

1. Add import at top: `from common.protocol import GameState`
2. In `_run_services` (line 84), change `heartbeat = HeartbeatService(` to `self._heartbeat = HeartbeatService(`
3. Replace all subsequent `heartbeat` references in `_run_services` with `self._heartbeat` (lines ~87, 94, 100, 104, 109, 118)

4. Replace `_process_monitor` method (lines 154-171):
```python
async def _process_monitor(self) -> None:
    """每 10s 检测 TestDemo.exe 是否存活，状态变化时上报 master"""
    was_running = False
    while self._running:
        running = self._is_testdemo_running()
        self.script_status.emit(running)
        if was_running and not running:
            try:
                self._heartbeat.send_status(GameState.SCRIPT_STOPPED)
            except Exception as e:
                print(f"[状态上报] 发送失败: {e}")
        was_running = running
        await asyncio.sleep(10)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_slave_fixes.py::TestHeartbeatSendStatus -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/slave/heartbeat.py src/slave/backend.py tests/test_slave_fixes.py
git commit -m "$(cat <<'EOF'
✨ feat(slave): TestDemo 崩溃时上报 master

heartbeat 新增 send_status() 方法（独立阻塞 UDP socket）。
_process_monitor 检测到 TestDemo 停止时发送 STATUS(脚本已停止)。
修复 #5 master 永久显示过期状态。
EOF
)"
```

---

### Task 6: C1 — DELETE_FILE 添加文件名输入

**Files:**
- Modify: `src/common/protocol.py:106` (build_tcp_command)
- Modify: `src/master/app/view/bigscreen_interface.py:644-661` (_deleteFileOnAll)

- [ ] **Step 1: Fix build_tcp_command to handle DELETE_FILE payload**

In `src/common/protocol.py`, change line 106 from:
```python
elif cmd in (TcpCommand.EXT_SET_GROUP, TcpCommand.DELETE_FILE) and payload:
```
Wait — check current code. Current line 106 is:
```python
elif cmd in (TcpCommand.EXT_SET_GROUP, TcpCommand.DELETE_FILE) and payload:
```
This already handles DELETE_FILE with payload. But actually looking at the current code (line 106):
```python
elif cmd in (TcpCommand.EXT_SET_GROUP, TcpCommand.DELETE_FILE) and payload:
```
This is already correct. The issue is just that `_deleteFileOnAll` doesn't pass a payload.

- [ ] **Step 2: Rewrite _deleteFileOnAll with file name input dialog**

In `src/master/app/view/bigscreen_interface.py`, replace `_deleteFileOnAll` (lines 644-661):

```python
def _deleteFileOnAll(self) -> None:
    """批量删除文件：弹窗输入文件名列表"""
    dlg = MessageBox("批量删除文件", "输入要删除的文件名（每行一个）", self.window())
    edit = PlainTextEdit(dlg)
    edit.setPlaceholderText("accounts.txt\nkey.txt\n...")
    edit.setMinimumHeight(120)
    dlg.textLayout.addWidget(edit)
    dlg.yesButton.setText("确认删除")
    dlg.cancelButton.setText("取消")
    if not dlg.exec():
        return
    filenames = [line.strip() for line in edit.toPlainText().splitlines() if line.strip()]
    if not filenames:
        InfoBar.warning(
            "提示", "未输入文件名",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )
        return
    ips, selected = self._getTargetIPs()
    if not ips:
        InfoBar.warning(
            "提示", "没有在线节点",
            parent=self, position=InfoBarPosition.TOP, duration=2000,
        )
        return
    scope = f"{len(ips)} 个{'选中' if selected else '在线'}节点"
    payload = "|".join(filenames)
    self._tcp.broadcast(ips, TcpCommand.DELETE_FILE, payload)
    self._nm.add_history("批量删除文件", scope, detail=", ".join(filenames))
    InfoBar.success(
        "已发送", f"删除指令已发送到 {scope}",
        parent=self, position=InfoBarPosition.TOP, duration=3000,
    )
```

- [ ] **Step 3: Run existing tests**

Run: `uv run pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/master/app/view/bigscreen_interface.py
git commit -m "$(cat <<'EOF'
🐛 fix(master): DELETE_FILE 添加文件名输入弹窗

修复批量删除文件无 payload 导致功能完全失效的问题。
EOF
)"
```

---

### Task 7: C2 — 清理 SENDFILE 死代码

**Files:**
- Modify: `src/common/protocol.py:96-99` (remove SENDFILE enums)
- Modify: `src/slave/command_handler.py` (remove _handle_sendfile, _read_chunks, MAX_FILE_SIZE)
- Modify: `tests/test_slave_fixes.py:164-168, 236-239`
- Modify: `tests/test_e2e_fixes.py:205-274, 352-354`

- [ ] **Step 1: Remove SENDFILE from protocol.py**

In `src/common/protocol.py`, remove lines 97-99:
```python
    SENDFILE_START = "SENDFILE_START"
    SENDFILE_CHUNK = "SENDFILE_CHUNK"
    SENDFILE_END = "SENDFILE_END"
```

- [ ] **Step 2: Remove SENDFILE handling from command_handler.py**

In `src/slave/command_handler.py`:
1. Remove `import tempfile` from imports (line 8)
2. Remove `MAX_FILE_SIZE = 100 * 1024 * 1024` (line 18)
3. In `_dispatch`, remove the SENDFILE branch (lines 140-141):
   ```python
   elif text.startswith("SENDFILE_START|"):
       desc = await self._handle_sendfile(text, reader)
   ```
4. Remove entire `_handle_sendfile` method (lines 211-264)
5. Remove entire `_read_chunks` method (lines 259-264 or wherever it is after deletion)
6. Add comment in `_dispatch` after the last elif:
   ```python
   # 注意: 文件下发使用 UPDATE_TXT 通道（base64 编码），无独立 SENDFILE 协议
   ```

- [ ] **Step 3: Update test_slave_fixes.py**

1. In `test_remaining_commands` (line 164-168), remove SENDFILE entries from expected set:
   ```python
   expected = {"UPDATE_TXT", "START_EXE", "STOP_EXE", "REBOOT_PC",
               "UPDATE_KEY", "DELETE_FILE", "EXT_SET_GROUP"}
   ```

2. Remove entire `test_max_file_size_in_sendfile_handler` test (lines 236-239)

3. Remove `test_max_file_size` test (lines 232-234) — it tests `MAX_FILE_SIZE` which is removed

- [ ] **Step 4: Update test_e2e_fixes.py**

1. Remove entire `TestSendFileE2E` class (lines 205-274)

2. In `TestProtocolCompatibility.test_all_commands_have_handlers` (lines 345-357), remove the SENDFILE source inspection:
   Replace with:
   ```python
   def test_all_commands_have_handlers(self):
       """每个 TcpCommand 的 value 都应在 slave _dispatch 中被处理"""
       import inspect
       from slave.command_handler import CommandHandler
       dispatch_source = inspect.getsource(CommandHandler._dispatch)
       for cmd in TcpCommand:
           assert cmd.value in dispatch_source, \
               f"TcpCommand.{cmd.name} ({cmd.value}) 在 _dispatch 中无对应 handler"
   ```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/common/protocol.py src/slave/command_handler.py tests/test_slave_fixes.py tests/test_e2e_fixes.py
git commit -m "$(cat <<'EOF'
♻️ refactor: 清理 SENDFILE 死代码

移除 SENDFILE_START/CHUNK/END 协议定义和 slave 端接收代码。
文件下发统一使用 UPDATE_TXT 通道。
EOF
)"
```

---

### Task 8: C3+C4+C5 — 按钮改名 + SET_GROUP 菜单 + dm 修正

**Files:**
- Modify: `src/master/app/view/bigscreen_interface.py` (button text, file filter, context menu)
- Modify: `src/slave/process_manager.py:14` (kill targets)

- [ ] **Step 1: Rename button and fix file filter**

In `src/master/app/view/bigscreen_interface.py`:

1. Change `_ACTION_BUTTONS` line 52 from:
   ```python
   ("一键下发文件", FIF.SEND_FILL, "btnSendFile"),
   ```
   to:
   ```python
   ("下发账号文件", FIF.SEND_FILL, "btnSendFile"),
   ```

2. In `_sendFileToAll` (line 617), change file dialog filter:
   ```python
   path, _ = QFileDialog.getOpenFileName(self, "选择账号文件", "", "Text (*.txt)")
   ```
   Add comment above:
   ```python
   # 通过 UPDATE_TXT 覆盖 slave 端 accounts.txt
   ```

- [ ] **Step 2: Add SET_GROUP to context menu**

In `_showNodeContextMenu`, after the "释放绑定账号"/"分配账号" section (around line 826), add before `menu.exec`:

```python
menu.addSeparator()
menu.addAction(
    Action(FIF.TAG, "设置分组",
           triggered=lambda: self._setNodeGroup(ip, machine_name))
)
```

Add new method:

```python
def _setNodeGroup(self, ip: str, machine_name: str) -> None:
    """设置节点分组"""
    dlg = MessageBox("设置分组", f"为 {machine_name} 设置分组名称", self.window())
    edit = PlainTextEdit(dlg)
    edit.setPlaceholderText("输入分组名...")
    edit.setMaximumHeight(40)
    dlg.textLayout.addWidget(edit)
    dlg.yesButton.setText("确认")
    if not dlg.exec():
        return
    group = edit.toPlainText().strip()
    if not group:
        return
    self._tcp.send(ip, TcpCommand.EXT_SET_GROUP, group)
    InfoBar.success(
        "已设置", f"{machine_name} → 分组 '{group}'",
        parent=self, position=InfoBarPosition.TOP, duration=2000,
    )
```

- [ ] **Step 3: Fix dm in process_manager.py**

In `src/slave/process_manager.py`:

1. Remove `"dm"` from `_KILL_TARGETS` (line 14)
2. Add `"dmsoft"` to `_KILL_KEYWORDS` (line 30):
   ```python
   _KILL_KEYWORDS = [
       "rapidocr",
       "dmsoft",  # 大漠插件相关进程
   ]
   ```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/master/app/view/bigscreen_interface.py src/slave/process_manager.py
git commit -m "$(cat <<'EOF'
🐛 fix: UI 操作修复合集

- 按钮"一键下发文件"改名为"下发账号文件"，限 .txt
- 节点右键菜单新增"设置分组"功能
- 修正 _KILL_TARGETS 中 dm 过于宽泛，改用 dmsoft 关键词匹配
EOF
)"
```

---

### Task 9: D1+D4+D5+D7+D8+D9+D10 — 技术债批量修复

**Files:**
- Modify: `src/master/app/core/account_db.py:111-116`
- Modify: `src/slave/backend.py:203-207`
- Modify: `src/slave/main.py:119-121`
- Modify: `src/common/protocol.py:72` (legacy comment)
- Modify: `src/master/app/view/bigscreen_interface.py:895-915` (watchdog)
- Modify: `src/master/app/core/udp_listener.py:14` (comment)
- Modify: `src/master/app/core/account_db.py:40` (TODO)

- [ ] **Step 1: Fix allocate ORDER BY**

In `src/master/app/core/account_db.py`, change `allocate` method SQL (lines 112-115):

From:
```python
"WHERE id = (SELECT id FROM accounts WHERE status='空闲中' LIMIT 1) "
```
To:
```python
"WHERE id = (SELECT id FROM accounts WHERE status='空闲中' ORDER BY id LIMIT 1) "
```

- [ ] **Step 2: Fix backend.stop() RuntimeError**

In `src/slave/backend.py`, change `stop` method (lines 203-207):

```python
def stop(self) -> None:
    """请求后台服务停止"""
    self._running = False
    if self._loop and self._loop.is_running():
        try:
            self._loop.call_soon_threadsafe(self._request_shutdown)
        except RuntimeError:
            pass  # loop 已关闭
```

- [ ] **Step 3: Fix main.py wait timeout**

In `src/slave/main.py`, replace lines 119-120:

From:
```python
if not backend.wait(5000):
    raise RuntimeError("SlaveBackend did not stop within 5 seconds")
```
To:
```python
if not backend.wait(5000):
    print("[警告] SlaveBackend 未在 5 秒内停止")
```

- [ ] **Step 4: Add legacy comment to build_udp_online**

In `src/common/protocol.py`, add comment before `build_udp_online` (line 72):

```python
def build_udp_online(machine_name: str, user_name: str) -> str:  # legacy: 仅测试使用，生产环境由 EXT_ONLINE 替代
```

- [ ] **Step 5: Fix watchdog to soft-restart first**

In `src/master/app/view/bigscreen_interface.py`, replace `_checkStaleNodes` (lines 896-915):

```python
def _checkStaleNodes(self) -> None:
    """检查停滞节点并自动软重启脚本"""
    threshold_min = self.spinTimeout.value()
    now = datetime.now()
    restarted = 0
    for node in self._nm.nodes.values():
        if node.status in ("离线", "断连"):
            continue
        if not node.game_state:  # 未启动脚本的节点跳过
            continue
        elapsed = (now - node.last_status_update).total_seconds() / 60
        if elapsed >= threshold_min:
            self._tcp.send(node.ip, TcpCommand.STOP_EXE)
            self._tcp.send(node.ip, TcpCommand.START_EXE)
            restarted += 1
    if restarted:
        self._nm.add_history("超时自动重启脚本", f"{restarted} 个节点")
        InfoBar.warning(
            "超时监控",
            f"已自动重启 {restarted} 个停滞节点的脚本",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )
```

- [ ] **Step 6: Add signal safety comment to udp_listener**

In `src/master/app/core/udp_listener.py`, change line 14:

```python
# 跨线程传递 Python 对象：emit 后不得修改对象内容
message_received = pyqtSignal(object, str)  # (UdpMessage, remote_ip)
```

- [ ] **Step 7: Add TODO to AccountDB**

In `src/master/app/core/account_db.py`, change class docstring (line 41):

```python
class AccountDB(QObject):
    """SQLite 持久化账号池，信号接口兼容 AccountPool

    TODO: 大规模部署（>5000 账号 + >100 节点）时应将 DB 操作移到工作线程
    """
```

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add src/master/app/core/account_db.py src/slave/backend.py src/slave/main.py \
    src/common/protocol.py src/master/app/view/bigscreen_interface.py \
    src/master/app/core/udp_listener.py
git commit -m "$(cat <<'EOF'
♻️ refactor: 技术债批量清理

- allocate 添加 ORDER BY id 保证分配顺序
- backend.stop() 捕获 RuntimeError
- main.py wait 超时改为 log 而非 raise
- build_udp_online 标注 legacy
- 超时监控改为软重启脚本（先 STOP+START，不直接重启电脑）
- 添加信号安全注释和 AccountDB TODO
EOF
)"
```

---

### Task 10: D2 — 删除 AccountPool + 迁移测试

**Files:**
- Delete: `src/master/app/core/account_pool.py`
- Delete: `tests/test_account_pool.py`
- Modify: `tests/test_integration.py:15,19-61`
- Modify: `tests/test_master_fixes.py:12,39-53,196-221`
- Modify: `master.spec:26`

- [ ] **Step 1: Migrate test_integration.py**

Replace import (line 15):
```python
# from master.app.core.account_pool import AccountPool
from master.app.core.account_db import AccountDB
```

Replace class (lines 19-61):
```python
class TestNodeManagerAccountDBIntegration:
    """NodeManager + AccountDB 协作"""

    def test_account_allocation_for_requesting_node(self, tmp_path):
        nm = NodeManager()
        pool = AccountDB(tmp_path / "test.db")
        pool.import_fresh("user1----pass1\nuser2----pass2\nuser3----pass3")

        for i in range(3):
            msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name=f"VM-{i:02d}", user_name="Admin")
            nm.handle_udp_message(msg, f"10.1.3.{i}")

        for name in nm.nodes:
            acc = pool.allocate(name)
            assert acc is not None

        assert pool.available_count == 0
        assert pool.in_use_count == 3

        pool.complete("VM-00", level=18)
        assert pool.completed_count == 1
        assert pool.in_use_count == 2
        pool.close()

    def test_node_timeout_releases_do_not_auto_release_accounts(self, tmp_path):
        """节点离线后账号不会自动释放"""
        nm = NodeManager()
        pool = AccountDB(tmp_path / "test.db")
        pool.import_fresh("user1----pass1")

        msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        nm.handle_udp_message(msg, "10.1.3.1")
        pool.allocate("VM-01")

        nm.nodes["VM-01"].last_seen = datetime.now() - timedelta(seconds=20)
        nm.check_timeouts()

        assert nm.nodes["VM-01"].status == "离线"
        assert pool.in_use_count == 1
        pool.close()
```

- [ ] **Step 2: Migrate test_master_fixes.py**

Replace import (line 12):
```python
# from master.app.core.account_pool import AccountPool
from master.app.core.account_db import AccountDB
```

Replace `TestC2FileErrorHandling` (lines 39-53):
```python
class TestC2FileErrorHandling:
    """验证 AccountDB.load_from_file 对不存在文件抛出 OSError"""

    def test_load_from_nonexistent_file(self, tmp_path):
        pool = AccountDB(tmp_path / "test.db")
        with pytest.raises(OSError, match="无法读取账号文件"):
            pool.load_from_file(tmp_path / "not_exist.txt")
        pool.close()

    def test_load_from_valid_file(self, tmp_path):
        f = tmp_path / "accounts.txt"
        f.write_text("user1----pass1\nuser2----pass2", encoding="utf-8")
        pool = AccountDB(tmp_path / "test.db")
        pool.load_from_file(f)
        assert pool.total_count == 2
        pool.close()
```

Replace `TestM7ExportTimestamp` (lines 196-221) — 使用 AccountDB API + 直接 SQL 设置 completed_at:
```python
class TestM7ExportTimestamp:
    """验证 export_completed 包含完成时间"""

    def test_export_includes_timestamp(self, tmp_path):
        pool = AccountDB(tmp_path / "test.db")
        pool.import_fresh("user1----pass1\nuser2----pass2")
        pool.allocate("VM-01")
        pool.complete("VM-01", level=30)
        # 直接 SQL 设置精确时间戳（complete() 使用 datetime.now()）
        pool._conn.execute(
            "UPDATE accounts SET completed_at='2026-03-18 14:30:00' WHERE username='user1'"
        )
        pool._conn.commit()

        result = pool.export_completed()
        assert "2026-03-18 14:30:00" in result
        assert "----30----" in result
        pool.close()

    def test_export_without_completed_at(self, tmp_path):
        pool = AccountDB(tmp_path / "test.db")
        pool.import_fresh("user1----pass1")
        pool.allocate("VM-01")
        # 直接 SQL 设置已完成但无时间
        pool._conn.execute(
            "UPDATE accounts SET status='已完成', completed_at=NULL WHERE username='user1'"
        )
        pool._conn.commit()
        pool._refresh_counts()

        result = pool.export_completed()
        assert "----无" in result
        pool.close()
```

- [ ] **Step 3: Delete old files**

```bash
rm src/master/app/core/account_pool.py tests/test_account_pool.py
```

- [ ] **Step 4: Remove from master.spec**

In `master.spec`, remove line 26:
```python
'master.app.core.account_pool',
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
♻️ refactor: 删除 AccountPool 旧版实现

迁移所有测试到 AccountDB，删除 account_pool.py。
生产代码已全面使用 AccountDB（SQLite 持久化）。
EOF
)"
```

---

## Execution Summary

| Task | Module | Issues Fixed | Estimated Steps |
|------|--------|-------------|-----------------|
| 1 | A1+A2 | #2,#3,#11 (partial) | 7 |
| 2 | A3 | #2,#3,#11 (core) | 5 |
| 3 | A4+A5 | #2,#3 (complete) | 4 |
| 4 | B1 | #1,#17 | 5 |
| 5 | B2+B3 | #5 | 6 |
| 6 | C1 | #4 | 4 |
| 7 | C2 | #6 | 6 |
| 8 | C3+C4+C5 | #7,#10,#15 | 5 |
| 9 | D1-D10 | #8,#14,#18,#19,#20,#21,D8 | 9 |
| 10 | D2 | #12 | 6 |
| **Total** | | **21 issues** | **57 steps** |
