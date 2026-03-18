# TriangleAlpha Master 重写实施计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 PyQt6 + PyQt-Fluent-Widgets 重写群控中控端和被控端，实现现代化 Fluent Design UI。

**Architecture:** 单仓库三层架构 — common(协议/模型) + master(PyQt6 GUI) + slave(纯asyncio)。网络线程与 UI 分离，通过 pyqtSignal 通信。

**Tech Stack:** Python 3.12, PyQt6, PyQt-Fluent-Widgets, psutil, asyncio, PyInstaller

**Spec:** `docs/superpowers/specs/2026-03-18-master-rewrite-design.md`

---

## Phase 1: 项目骨架 + 共享层

### Task 1: 项目初始化

**Files:**
- Create: `pyproject.toml`
- Create: `src/common/__init__.py`
- Create: `src/master/__init__.py`
- Create: `src/slave/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: 创建 pyproject.toml**

```toml
[project]
name = "triangle-alpha-master"
version = "0.1.0"
description = "TriangleAlpha 群控系统"
requires-python = ">=3.12"
dependencies = [
    "PyQt6>=6.7",
    "PyQt-Fluent-Widgets>=1.7.0",
    "psutil>=5.9",
]

[project.scripts]
master = "master.main:main"
slave = "slave.main:main"

[dependency-groups]
dev = [
    "ruff",
    "mypy",
    "pytest",
    "pyinstaller>=6.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/common", "src/master", "src/slave"]

[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = false
warn_return_any = true
ignore_missing_imports = true

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: 创建目录结构和 __init__.py**

```bash
mkdir -p src/common src/master/app/common src/master/app/core src/master/app/view src/master/app/components src/master/app/resource/qss/light src/master/app/resource/qss/dark src/slave tests
touch src/common/__init__.py src/master/__init__.py src/master/app/__init__.py src/master/app/common/__init__.py src/master/app/core/__init__.py src/master/app/view/__init__.py src/master/app/components/__init__.py src/slave/__init__.py tests/__init__.py
```

- [ ] **Step 3: 安装依赖并验证**

```bash
cd /Users/daoji/Code/DeltaForce/TriangleAlpha-Master
uv sync
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "🔨 build: 初始化项目骨架和依赖"
```

---

### Task 2: 共享数据模型

**Files:**
- Create: `src/common/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_models.py
from datetime import datetime, timedelta
from common.models import NodeInfo, AccountInfo, AccountStatus


class TestNodeInfo:
    def test_create_minimal(self):
        node = NodeInfo(machine_name="VM-01", ip="10.1.3.51")
        assert node.machine_name == "VM-01"
        assert node.ip == "10.1.3.51"
        assert node.status == "在线"
        assert node.group == "默认"

    def test_is_online_within_threshold(self):
        node = NodeInfo(machine_name="VM-01", ip="10.1.3.51")
        assert node.is_online(timeout_sec=15)

    def test_is_offline_after_timeout(self):
        node = NodeInfo(
            machine_name="VM-01", ip="10.1.3.51",
            last_seen=datetime.now() - timedelta(seconds=20),
        )
        assert not node.is_online(timeout_sec=15)


class TestAccountInfo:
    def test_create_from_line(self):
        acc = AccountInfo.from_line("user1----pass1")
        assert acc.username == "user1"
        assert acc.password == "pass1"
        assert acc.status == AccountStatus.IDLE

    def test_masked_password(self):
        acc = AccountInfo(username="u", password="secret123")
        assert acc.masked_password == "••••••••"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_models.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'common.models'`

- [ ] **Step 3: 实现模型**

```python
# src/common/models.py
"""共享数据模型"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class AccountStatus(enum.Enum):
    IDLE = "空闲"
    IN_USE = "使用中"
    COMPLETED = "已完成"


@dataclass
class NodeInfo:
    """被控端节点信息"""
    machine_name: str
    ip: str
    user_name: str = ""
    status: str = "在线"
    level: int = 0
    jin_bi: str = "0"
    current_account: str = ""
    group: str = "默认"
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    slave_version: str = ""
    last_seen: datetime = field(default_factory=datetime.now)
    last_status_update: datetime = field(default_factory=datetime.now)

    def is_online(self, timeout_sec: int = 15) -> bool:
        return (datetime.now() - self.last_seen).total_seconds() < timeout_sec


@dataclass
class AccountInfo:
    """账号信息"""
    username: str
    password: str = ""
    status: AccountStatus = AccountStatus.IDLE
    assigned_machine: str = ""
    level: int = 0
    completed_at: datetime | None = None

    @classmethod
    def from_line(cls, line: str) -> AccountInfo:
        parts = line.strip().split("----", maxsplit=1)
        username = parts[0].strip()
        password = parts[1].strip() if len(parts) > 1 else ""
        return cls(username=username, password=password)

    @property
    def masked_password(self) -> str:
        return "••••••••" if self.password else ""


@dataclass
class OperationRecord:
    """操作历史记录"""
    timestamp: datetime
    op_type: str
    target: str
    detail: str = ""
    result: str = ""
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_models.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/common/models.py tests/test_models.py
git commit -m "✨ feat(common): 添加共享数据模型"
```

---

### Task 3: 协议层

**Files:**
- Create: `src/common/protocol.py`
- Create: `tests/test_protocol.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_protocol.py
from common.protocol import (
    parse_udp_message, build_udp_online, build_udp_status,
    build_tcp_command, UdpMessageType, TcpCommand,
)


class TestParseUdp:
    def test_parse_online(self):
        msg = parse_udp_message("ONLINE|VM-01|Admin")
        assert msg.type == UdpMessageType.ONLINE
        assert msg.machine_name == "VM-01"
        assert msg.user_name == "Admin"

    def test_parse_status(self):
        msg = parse_udp_message("STATUS|VM-01|升级中|18|12450|正在升级")
        assert msg.type == UdpMessageType.STATUS
        assert msg.machine_name == "VM-01"
        assert msg.state == "升级中"
        assert msg.level == 18
        assert msg.jin_bi == "12450"
        assert msg.desc == "正在升级"

    def test_parse_offline(self):
        msg = parse_udp_message("OFFLINE|VM-01")
        assert msg.type == UdpMessageType.OFFLINE
        assert msg.machine_name == "VM-01"

    def test_parse_ext_online(self):
        msg = parse_udp_message("EXT_ONLINE|VM-01|Admin|45.2|60.1|1.0.0|A组")
        assert msg.type == UdpMessageType.EXT_ONLINE
        assert msg.cpu_percent == 45.2
        assert msg.mem_percent == 60.1
        assert msg.slave_version == "1.0.0"
        assert msg.group == "A组"

    def test_parse_unknown_returns_none(self):
        msg = parse_udp_message("GARBAGE|data")
        assert msg is None


class TestBuildUdp:
    def test_build_online(self):
        raw = build_udp_online("VM-01", "Admin")
        assert raw == "ONLINE|VM-01|Admin"

    def test_build_status(self):
        raw = build_udp_status("VM-01", "升级中", 18, "12450", "正在升级")
        assert raw == "STATUS|VM-01|升级中|18|12450|正在升级"


class TestBuildTcp:
    def test_start_exe(self):
        assert build_tcp_command(TcpCommand.START_EXE) == "STARTEXE|"

    def test_stop_exe(self):
        assert build_tcp_command(TcpCommand.STOP_EXE) == "STOPEXE|"

    def test_reboot_pc(self):
        assert build_tcp_command(TcpCommand.REBOOT_PC) == "REBOOTPC|"

    def test_update_key(self):
        cmd = build_tcp_command(TcpCommand.UPDATE_KEY, payload="MYKEY123")
        # payload 应该 base64 编码
        import base64
        parts = cmd.split("|", 1)
        assert parts[0] == "UPDATEKEY"
        decoded = base64.b64decode(parts[1]).decode("utf-8")
        assert decoded == "MYKEY123"

    def test_update_txt(self):
        cmd = build_tcp_command(TcpCommand.UPDATE_TXT, payload="user----pass")
        import base64
        parts = cmd.split("|", 1)
        assert parts[0] == "UPDATETXT"
        decoded = base64.b64decode(parts[1]).decode("utf-8")
        assert decoded == "user----pass"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_protocol.py -v
```

- [ ] **Step 3: 实现协议层**

```python
# src/common/protocol.py
"""通信协议：消息解析、构建、常量定义"""
from __future__ import annotations

import base64
import enum
from dataclasses import dataclass


# ═══════════════ 常量 ═══════════════

UDP_PORT = 8888
TCP_CMD_PORT = 9999
TCP_LOG_PORT = 8890
HEARTBEAT_INTERVAL = 3  # 秒
OFFLINE_TIMEOUT = 15    # 秒
DISCONNECT_TIMEOUT = 60 # 秒
TCP_SEND_TIMEOUT = 10   # 秒


# ═══════════════ UDP 消息 ═══════════════

class UdpMessageType(enum.Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    STATUS = "STATUS"
    EXT_ONLINE = "EXT_ONLINE"


@dataclass
class UdpMessage:
    type: UdpMessageType
    machine_name: str = ""
    user_name: str = ""
    # STATUS 字段
    state: str = ""
    level: int = 0
    jin_bi: str = "0"
    desc: str = ""
    # EXT_ONLINE 字段
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    slave_version: str = ""
    group: str = "默认"


def parse_udp_message(raw: str) -> UdpMessage | None:
    """解析 UDP 消息字符串，返回 UdpMessage 或 None"""
    parts = raw.split("|")
    if not parts:
        return None

    type_str = parts[0]

    if type_str == "ONLINE" and len(parts) >= 3:
        return UdpMessage(
            type=UdpMessageType.ONLINE,
            machine_name=parts[1],
            user_name=parts[2],
        )
    elif type_str == "OFFLINE" and len(parts) >= 2:
        return UdpMessage(
            type=UdpMessageType.OFFLINE,
            machine_name=parts[1],
        )
    elif type_str == "STATUS" and len(parts) >= 6:
        return UdpMessage(
            type=UdpMessageType.STATUS,
            machine_name=parts[1],
            state=parts[2],
            level=int(parts[3]) if parts[3].isdigit() else 0,
            jin_bi=parts[4],
            desc=parts[5],
        )
    elif type_str == "EXT_ONLINE" and len(parts) >= 7:
        return UdpMessage(
            type=UdpMessageType.EXT_ONLINE,
            machine_name=parts[1],
            user_name=parts[2],
            cpu_percent=float(parts[3]) if parts[3].replace(".", "").isdigit() else 0.0,
            mem_percent=float(parts[4]) if parts[4].replace(".", "").isdigit() else 0.0,
            slave_version=parts[5],
            group=parts[6],
        )
    return None


def build_udp_online(machine_name: str, user_name: str) -> str:
    return f"ONLINE|{machine_name}|{user_name}"


def build_udp_ext_online(
    machine_name: str, user_name: str,
    cpu: float, mem: float, version: str, group: str,
) -> str:
    return f"EXT_ONLINE|{machine_name}|{user_name}|{cpu:.1f}|{mem:.1f}|{version}|{group}"


def build_udp_offline(machine_name: str) -> str:
    return f"OFFLINE|{machine_name}"


def build_udp_status(
    machine_name: str, state: str, level: int, jin_bi: str, desc: str,
) -> str:
    return f"STATUS|{machine_name}|{state}|{level}|{jin_bi}|{desc}"


# ═══════════════ TCP 指令 ═══════════════

class TcpCommand(enum.Enum):
    UPDATE_TXT = "UPDATETXT"
    START_EXE = "STARTEXE"
    STOP_EXE = "STOPEXE"
    REBOOT_PC = "REBOOTPC"
    UPDATE_KEY = "UPDATEKEY"
    DELETE_FILE = "DELETEFILE"
    # 文件传输由 file_transfer 模块处理
    # 新增
    EXT_QUERY = "EXT_QUERY"
    EXT_SET_GROUP = "EXT_SETGROUP"


def build_tcp_command(cmd: TcpCommand, payload: str = "") -> str:
    """构建 TCP 指令字符串。payload 自动 base64 编码（如需要）"""
    if cmd in (TcpCommand.UPDATE_TXT, TcpCommand.UPDATE_KEY) and payload:
        encoded = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
        return f"{cmd.value}|{encoded}"
    elif cmd == TcpCommand.EXT_SET_GROUP and payload:
        return f"{cmd.value}|{payload}"
    elif cmd == TcpCommand.DELETE_FILE and payload:
        return f"{cmd.value}|{payload}"
    else:
        return f"{cmd.value}|"
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_protocol.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/common/protocol.py tests/test_protocol.py
git commit -m "✨ feat(common): 添加协议解析和构建"
```

---

## Phase 2: 中控核心层

### Task 4: NodeManager — 节点状态管理

**Files:**
- Create: `src/master/app/core/node_manager.py`
- Create: `tests/test_node_manager.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_node_manager.py
from unittest.mock import MagicMock
from datetime import datetime, timedelta
from master.app.core.node_manager import NodeManager
from common.protocol import UdpMessage, UdpMessageType


class TestNodeManager:
    def setup_method(self):
        self.nm = NodeManager()

    def test_handle_online_adds_node(self):
        msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        self.nm.handle_udp_message(msg, "10.1.3.51")
        assert "VM-01" in self.nm.nodes
        assert self.nm.nodes["VM-01"].ip == "10.1.3.51"

    def test_handle_online_updates_existing(self):
        msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        self.nm.handle_udp_message(msg, "10.1.3.51")
        self.nm.handle_udp_message(msg, "10.1.3.99")
        assert self.nm.nodes["VM-01"].ip == "10.1.3.99"

    def test_handle_offline(self):
        msg_on = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        self.nm.handle_udp_message(msg_on, "10.1.3.51")
        msg_off = UdpMessage(type=UdpMessageType.OFFLINE, machine_name="VM-01")
        self.nm.handle_udp_message(msg_off, "10.1.3.51")
        assert self.nm.nodes["VM-01"].status == "离线"

    def test_handle_status(self):
        msg_on = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        self.nm.handle_udp_message(msg_on, "10.1.3.51")
        msg_st = UdpMessage(
            type=UdpMessageType.STATUS, machine_name="VM-01",
            state="升级中", level=18, jin_bi="12450", desc="正在升级",
        )
        self.nm.handle_udp_message(msg_st, "10.1.3.51")
        node = self.nm.nodes["VM-01"]
        assert node.level == 18
        assert node.jin_bi == "12450"

    def test_check_timeouts(self):
        msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-01", user_name="Admin")
        self.nm.handle_udp_message(msg, "10.1.3.51")
        self.nm.nodes["VM-01"].last_seen = datetime.now() - timedelta(seconds=20)
        self.nm.check_timeouts()
        assert self.nm.nodes["VM-01"].status == "离线"

    def test_online_count(self):
        for i in range(5):
            msg = UdpMessage(type=UdpMessageType.ONLINE, machine_name=f"VM-{i:02d}", user_name="A")
            self.nm.handle_udp_message(msg, f"10.1.3.{i}")
        self.nm.nodes["VM-00"].last_seen = datetime.now() - timedelta(seconds=20)
        self.nm.check_timeouts()
        assert self.nm.online_count == 4
        assert self.nm.total_count == 5

    def test_get_nodes_by_group(self):
        msg = UdpMessage(type=UdpMessageType.EXT_ONLINE, machine_name="VM-01",
                         user_name="A", group="A组")
        self.nm.handle_udp_message(msg, "10.1.3.51")
        msg2 = UdpMessage(type=UdpMessageType.ONLINE, machine_name="VM-02", user_name="B")
        self.nm.handle_udp_message(msg2, "10.1.3.52")
        assert len(self.nm.get_nodes_by_group("A组")) == 1
        assert len(self.nm.get_nodes_by_group("默认")) == 1
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_node_manager.py -v
```

- [ ] **Step 3: 实现 NodeManager**

```python
# src/master/app/core/node_manager.py
"""节点状态管理器 — 中控核心单例"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from common.models import NodeInfo, OperationRecord
from common.protocol import UdpMessage, UdpMessageType, OFFLINE_TIMEOUT, DISCONNECT_TIMEOUT


class NodeManager(QObject):
    node_updated = pyqtSignal(str)       # machine_name
    node_online = pyqtSignal(str)        # 新节点上线
    node_offline = pyqtSignal(str)       # 节点离线
    stats_changed = pyqtSignal()         # 统计变化

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.nodes: dict[str, NodeInfo] = {}
        self.history: list[OperationRecord] = []

    # ── 属性 ──

    @property
    def online_count(self) -> int:
        return sum(1 for n in self.nodes.values() if n.status not in ("离线", "断线"))

    @property
    def total_count(self) -> int:
        return len(self.nodes)

    def get_nodes_by_group(self, group: str) -> list[NodeInfo]:
        return [n for n in self.nodes.values() if n.group == group]

    @property
    def groups(self) -> list[str]:
        return sorted({n.group for n in self.nodes.values()})

    # ── UDP 消息处理 ──

    def handle_udp_message(self, msg: UdpMessage, remote_ip: str) -> None:
        if msg.type == UdpMessageType.ONLINE:
            self._handle_online(msg, remote_ip)
        elif msg.type == UdpMessageType.EXT_ONLINE:
            self._handle_ext_online(msg, remote_ip)
        elif msg.type == UdpMessageType.OFFLINE:
            self._handle_offline(msg)
        elif msg.type == UdpMessageType.STATUS:
            self._handle_status(msg, remote_ip)

    def _handle_online(self, msg: UdpMessage, ip: str) -> None:
        name = msg.machine_name
        if name in self.nodes:
            node = self.nodes[name]
            node.ip = ip
            node.user_name = msg.user_name
            node.last_seen = datetime.now()
            if node.status in ("离线", "断线"):
                node.status = "在线"
            self.node_updated.emit(name)
        else:
            self.nodes[name] = NodeInfo(
                machine_name=name, ip=ip, user_name=msg.user_name,
            )
            self.node_online.emit(name)
        self.stats_changed.emit()

    def _handle_ext_online(self, msg: UdpMessage, ip: str) -> None:
        self._handle_online(msg, ip)
        node = self.nodes[msg.machine_name]
        node.cpu_percent = msg.cpu_percent
        node.mem_percent = msg.mem_percent
        node.slave_version = msg.slave_version
        node.group = msg.group
        self.node_updated.emit(msg.machine_name)

    def _handle_offline(self, msg: UdpMessage) -> None:
        name = msg.machine_name
        if name in self.nodes:
            self.nodes[name].status = "离线"
            self.node_offline.emit(name)
            self.stats_changed.emit()

    def _handle_status(self, msg: UdpMessage, ip: str) -> None:
        name = msg.machine_name
        if name not in self.nodes:
            self.nodes[name] = NodeInfo(machine_name=name, ip=ip)
            self.node_online.emit(name)
        node = self.nodes[name]
        node.current_account = msg.state
        node.level = msg.level
        node.jin_bi = msg.jin_bi
        node.status = msg.desc
        node.last_seen = datetime.now()
        node.last_status_update = datetime.now()
        self.node_updated.emit(name)
        self.stats_changed.emit()

    # ── 超时检查 ──

    def check_timeouts(self) -> None:
        now = datetime.now()
        for node in self.nodes.values():
            elapsed = (now - node.last_seen).total_seconds()
            if elapsed > DISCONNECT_TIMEOUT and node.status != "断线":
                node.status = "断线"
                self.node_offline.emit(node.machine_name)
            elif OFFLINE_TIMEOUT < elapsed <= DISCONNECT_TIMEOUT and node.status not in ("离线", "断线"):
                node.status = "离线"
                self.node_offline.emit(node.machine_name)
        self.stats_changed.emit()

    # ── 操作历史 ──

    def add_history(self, op_type: str, target: str, detail: str = "", result: str = "") -> None:
        self.history.append(OperationRecord(
            timestamp=datetime.now(), op_type=op_type, target=target,
            detail=detail, result=result,
        ))
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_node_manager.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/master/app/core/node_manager.py tests/test_node_manager.py
git commit -m "✨ feat(master): 添加 NodeManager 节点状态管理器"
```

---

### Task 5: UDP 监听线程 + TCP 指令发送

**Files:**
- Create: `src/master/app/core/udp_listener.py`
- Create: `src/master/app/core/tcp_commander.py`

- [ ] **Step 1: 实现 UdpListenerThread**

```python
# src/master/app/core/udp_listener.py
"""UDP 心跳/状态监听线程"""
from __future__ import annotations

import socket
import traceback

from PyQt6.QtCore import QThread, pyqtSignal

from common.protocol import UDP_PORT, parse_udp_message, UdpMessage


class UdpListenerThread(QThread):
    message_received = pyqtSignal(object, str)  # (UdpMessage, remote_ip)
    error_occurred = pyqtSignal(str)

    def __init__(self, port: int = UDP_PORT, parent=None):
        super().__init__(parent)
        self._port = port
        self._running = True

    def run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(2.0)
            sock.bind(("0.0.0.0", self._port))
        except OSError as e:
            self.error_occurred.emit(f"UDP 绑定端口 {self._port} 失败: {e}")
            return

        while self._running:
            try:
                data, addr = sock.recvfrom(4096)
                raw = data.decode("utf-8", errors="ignore")
                msg = parse_udp_message(raw)
                if msg is not None:
                    self.message_received.emit(msg, addr[0])
            except socket.timeout:
                continue
            except Exception:
                self.error_occurred.emit(traceback.format_exc())

        sock.close()

    def stop(self) -> None:
        self._running = False
        self.wait(3000)
```

- [ ] **Step 2: 实现 TcpCommander**

```python
# src/master/app/core/tcp_commander.py
"""TCP 指令发送器"""
from __future__ import annotations

import socket
import base64
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from common.protocol import TCP_CMD_PORT, TCP_SEND_TIMEOUT, build_tcp_command, TcpCommand


class _SendTask(QRunnable):
    """线程池任务：发送单条 TCP 指令"""

    def __init__(self, ip: str, command: str, port: int, timeout: int,
                 on_success, on_error):
        super().__init__()
        self._ip = ip
        self._command = command
        self._port = port
        self._timeout = timeout
        self._on_success = on_success
        self._on_error = on_error

    def run(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(self._timeout)
                sock.connect((self._ip, self._port))
                sock.sendall((self._command + "\n").encode("utf-8"))
            self._on_success(self._ip, self._command)
        except Exception as e:
            self._on_error(self._ip, str(e))


class TcpCommander(QObject):
    command_sent = pyqtSignal(str, str)     # (ip, command)
    command_failed = pyqtSignal(str, str)   # (ip, error)

    def __init__(self, port: int = TCP_CMD_PORT, parent=None):
        super().__init__(parent)
        self._port = port
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(20)

    def send(self, ip: str, cmd: TcpCommand, payload: str = "") -> None:
        command_str = build_tcp_command(cmd, payload)
        task = _SendTask(
            ip, command_str, self._port, TCP_SEND_TIMEOUT,
            on_success=lambda ip, cmd: self.command_sent.emit(ip, cmd),
            on_error=lambda ip, err: self.command_failed.emit(ip, err),
        )
        self._pool.start(task)

    def send_raw(self, ip: str, raw_command: str) -> None:
        task = _SendTask(
            ip, raw_command, self._port, TCP_SEND_TIMEOUT,
            on_success=lambda ip, cmd: self.command_sent.emit(ip, cmd),
            on_error=lambda ip, err: self.command_failed.emit(ip, err),
        )
        self._pool.start(task)

    def broadcast(self, ips: list[str], cmd: TcpCommand, payload: str = "") -> None:
        for ip in ips:
            self.send(ip, cmd, payload)
```

- [ ] **Step 3: Commit**

```bash
git add src/master/app/core/udp_listener.py src/master/app/core/tcp_commander.py
git commit -m "✨ feat(master): 添加 UDP 监听线程和 TCP 指令发送器"
```

---

### Task 6: 账号池管理

**Files:**
- Create: `src/master/app/core/account_pool.py`
- Create: `tests/test_account_pool.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_account_pool.py
from master.app.core.account_pool import AccountPool
from common.models import AccountStatus


class TestAccountPool:
    def test_load_from_text(self):
        text = "user1----pass1\nuser2----pass2\n\nuser3----pass3"
        pool = AccountPool()
        pool.load_from_text(text)
        assert pool.total_count == 3

    def test_allocate(self):
        pool = AccountPool()
        pool.load_from_text("u1----p1\nu2----p2")
        acc = pool.allocate("VM-01")
        assert acc is not None
        assert acc.username == "u1"
        assert acc.status == AccountStatus.IN_USE
        assert acc.assigned_machine == "VM-01"
        assert pool.available_count == 1

    def test_allocate_skips_used(self):
        pool = AccountPool()
        pool.load_from_text("u1----p1\nu2----p2")
        pool.allocate("VM-01")
        acc = pool.allocate("VM-02")
        assert acc.username == "u2"

    def test_allocate_returns_none_when_empty(self):
        pool = AccountPool()
        pool.load_from_text("u1----p1")
        pool.allocate("VM-01")
        assert pool.allocate("VM-02") is None

    def test_complete(self):
        pool = AccountPool()
        pool.load_from_text("u1----p1")
        pool.allocate("VM-01")
        pool.complete("VM-01", level=18)
        acc = pool.accounts[0]
        assert acc.status == AccountStatus.COMPLETED
        assert acc.level == 18
        assert pool.completed_count == 1

    def test_release(self):
        pool = AccountPool()
        pool.load_from_text("u1----p1")
        pool.allocate("VM-01")
        pool.release("VM-01")
        assert pool.available_count == 1
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_account_pool.py -v
```

- [ ] **Step 3: 实现 AccountPool**

```python
# src/master/app/core/account_pool.py
"""账号池管理"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from common.models import AccountInfo, AccountStatus


class AccountPool(QObject):
    pool_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.accounts: list[AccountInfo] = []

    # ── 属性 ──

    @property
    def total_count(self) -> int:
        return len(self.accounts)

    @property
    def available_count(self) -> int:
        return sum(1 for a in self.accounts if a.status == AccountStatus.IDLE)

    @property
    def in_use_count(self) -> int:
        return sum(1 for a in self.accounts if a.status == AccountStatus.IN_USE)

    @property
    def completed_count(self) -> int:
        return sum(1 for a in self.accounts if a.status == AccountStatus.COMPLETED)

    # ── 加载 ──

    def load_from_text(self, text: str) -> int:
        self.accounts.clear()
        for line in text.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            self.accounts.append(AccountInfo.from_line(line))
        self.pool_changed.emit()
        return len(self.accounts)

    def load_from_file(self, path: str) -> int:
        with open(path, encoding="utf-8") as f:
            return self.load_from_text(f.read())

    # ── 分配 ──

    def allocate(self, machine_name: str) -> AccountInfo | None:
        for acc in self.accounts:
            if acc.status == AccountStatus.IDLE:
                acc.status = AccountStatus.IN_USE
                acc.assigned_machine = machine_name
                self.pool_changed.emit()
                return acc
        return None

    def complete(self, machine_name: str, level: int = 0) -> None:
        for acc in self.accounts:
            if acc.status == AccountStatus.IN_USE and acc.assigned_machine == machine_name:
                acc.status = AccountStatus.COMPLETED
                acc.level = level
                acc.completed_at = datetime.now()
                self.pool_changed.emit()
                return

    def release(self, machine_name: str) -> None:
        for acc in self.accounts:
            if acc.status == AccountStatus.IN_USE and acc.assigned_machine == machine_name:
                acc.status = AccountStatus.IDLE
                acc.assigned_machine = ""
                self.pool_changed.emit()
                return

    # ── 导出 ──

    def export_completed(self) -> str:
        lines = []
        for acc in self.accounts:
            if acc.status == AccountStatus.COMPLETED:
                time_str = acc.completed_at.strftime("%Y-%m-%d %H:%M:%S") if acc.completed_at else ""
                lines.append(f"{time_str} | {acc.username} | {acc.assigned_machine} | Lv.{acc.level}")
        return "\n".join(lines)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_account_pool.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/master/app/core/account_pool.py tests/test_account_pool.py
git commit -m "✨ feat(master): 添加账号池管理"
```

---

## Phase 3: 中控 UI

### Task 7: UI 基础设施（config, signal_bus, style_sheet）

**Files:**
- Create: `src/master/app/common/config.py`
- Create: `src/master/app/common/signal_bus.py`
- Create: `src/master/app/common/style_sheet.py`

- [ ] **Step 1: 创建 config.py**

```python
# src/master/app/common/config.py
"""应用配置"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from qfluentwidgets import (
    BoolValidator, ConfigItem, QConfig, RangeConfigItem,
    RangeValidator, OptionsConfigItem, OptionsValidator,
    qconfig, Theme,
)

RESOURCE_DIR = Path(__file__).parent.parent / "resource"
CONFIG_DIR = Path.home() / ".triangle-alpha"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


class AppConfig(QConfig):
    # 网络
    udpPort = RangeConfigItem("Network", "UdpPort", 8888, RangeValidator(1024, 65535))
    tcpCmdPort = RangeConfigItem("Network", "TcpCmdPort", 9999, RangeValidator(1024, 65535))
    tcpLogPort = RangeConfigItem("Network", "TcpLogPort", 8890, RangeValidator(1024, 65535))
    heartbeatInterval = RangeConfigItem("Network", "HeartbeatInterval", 3, RangeValidator(1, 30))
    offlineTimeout = RangeConfigItem("Network", "OfflineTimeout", 15, RangeValidator(5, 120))

    # 外观
    micaEnabled = ConfigItem("UI", "MicaEnabled", False, BoolValidator())


cfg = AppConfig()
qconfig.load(str(CONFIG_DIR / "config.json"), cfg)
```

- [ ] **Step 2: 创建 signal_bus.py**

```python
# src/master/app/common/signal_bus.py
"""全局信号总线"""
from PyQt6.QtCore import QObject, pyqtSignal


class SignalBus(QObject):
    # 节点操作
    start_nodes = pyqtSignal(list)       # [machine_name, ...]
    stop_nodes = pyqtSignal(list)
    reboot_nodes = pyqtSignal(list)
    reboot_pc_nodes = pyqtSignal(list)
    distribute_keys = pyqtSignal()
    send_file = pyqtSignal(str, list)    # (file_path, [machine_name, ...])

    # UI
    micaEnableChanged = pyqtSignal(bool)
    switch_to_node = pyqtSignal(str)     # 跳转到节点详情


signalBus = SignalBus()
```

- [ ] **Step 3: 创建 style_sheet.py**

```python
# src/master/app/common/style_sheet.py
"""QSS 样式表管理"""
from enum import Enum

from qfluentwidgets import StyleSheetBase, Theme, qconfig

from master.app.common.config import RESOURCE_DIR


class StyleSheet(StyleSheetBase, Enum):
    NODE_INTERFACE = "node_interface"
    DASHBOARD_INTERFACE = "dashboard_interface"
    ACCOUNT_INTERFACE = "account_interface"
    LOG_INTERFACE = "log_interface"

    def path(self, theme=Theme.AUTO):
        theme = qconfig.theme if theme == Theme.AUTO else theme
        return str(RESOURCE_DIR / "qss" / theme.value.lower() / f"{self.value}.qss")
```

- [ ] **Step 4: 创建基础 QSS**

创建 `src/master/app/resource/qss/light/node_interface.qss`:
```css
QWidget#view { background-color: transparent; }
QScrollArea { border: none; background-color: transparent; }
#statCard { border-radius: 8px; background-color: rgba(249, 249, 249, 0.95); border: 1px solid rgb(234, 234, 234); }
#statTitle { color: rgb(96, 96, 96); font-size: 12px; }
#statValue { color: black; font-size: 28px; font-weight: bold; }
```

创建 `src/master/app/resource/qss/dark/node_interface.qss`:
```css
QWidget#view { background-color: transparent; }
QScrollArea { border: none; background-color: transparent; }
#statCard { border-radius: 8px; background-color: rgba(39, 39, 39, 0.95); border: 1px solid rgb(60, 60, 60); }
#statTitle { color: rgb(170, 170, 170); font-size: 12px; }
#statValue { color: white; font-size: 28px; font-weight: bold; }
```

复制以上 QSS 为 `dashboard_interface.qss`, `account_interface.qss`, `log_interface.qss`（light 和 dark 各一份）。

- [ ] **Step 5: Commit**

```bash
git add src/master/app/common/ src/master/app/resource/
git commit -m "✨ feat(master): 添加 UI 基础设施（config, signal_bus, style_sheet, QSS）"
```

---

### Task 8: StatCard 组件 + 主窗口

**Files:**
- Create: `src/master/app/components/stat_card.py`
- Create: `src/master/app/view/main_window.py`
- Create: `src/master/main.py`

- [ ] **Step 1: 创建 StatCard**

```python
# src/master/app/components/stat_card.py
"""统计卡片组件"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout
from qfluentwidgets import SimpleCardWidget


class StatCard(SimpleCardWidget):
    def __init__(self, title: str, value: str = "0", parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setFixedHeight(110)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)

        self.titleLabel = QLabel(title, self)
        self.titleLabel.setObjectName("statTitle")
        layout.addWidget(self.titleLabel)

        layout.addStretch(1)

        self.valueLabel = QLabel(value, self)
        self.valueLabel.setObjectName("statValue")
        layout.addWidget(self.valueLabel)

    def setValue(self, value: str) -> None:
        self.valueLabel.setText(value)
```

- [ ] **Step 2: 创建占位页面（后续 Task 补全）**

为每个页面创建最小占位（`ScrollArea` + 标题）：

```python
# src/master/app/view/dashboard_interface.py
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from qfluentwidgets import ScrollArea

class DashboardInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dashboardInterface")
        self.view = QWidget(self)
        layout = QVBoxLayout(self.view)
        layout.addWidget(QLabel("仪表盘 — 待实现"))
        self.setWidget(self.view)
        self.setWidgetResizable(True)
```

同样创建 `node_interface.py`, `account_interface.py`, `history_interface.py`, `log_interface.py`, `setting_interface.py` 的占位版本（结构相同，修改 objectName 和标题文字）。

- [ ] **Step 3: 创建 MainWindow**

```python
# src/master/app/view/main_window.py
"""主窗口"""
from __future__ import annotations

import sys

from PyQt6.QtCore import QSize, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF, FluentWindow, NavigationItemPosition, SplashScreen

from master.app.common.config import cfg
from master.app.common.signal_bus import signalBus
from master.app.core.node_manager import NodeManager
from master.app.core.udp_listener import UdpListenerThread
from master.app.core.tcp_commander import TcpCommander
from master.app.core.account_pool import AccountPool
from master.app.view.dashboard_interface import DashboardInterface
from master.app.view.node_interface import NodeInterface
from master.app.view.account_interface import AccountInterface
from master.app.view.history_interface import HistoryInterface
from master.app.view.log_interface import LogInterface
from master.app.view.setting_interface import SettingInterface


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()

        # 核心服务
        self.nodeManager = NodeManager(self)
        self.tcpCommander = TcpCommander(port=cfg.get(cfg.tcpCmdPort), parent=self)
        self.accountPool = AccountPool(self)
        self.udpListener = UdpListenerThread(port=cfg.get(cfg.udpPort), parent=self)

        # 连接 UDP → NodeManager
        self.udpListener.message_received.connect(self.nodeManager.handle_udp_message)

        # 页面
        self.dashboardInterface = DashboardInterface(self)
        self.nodeInterface = NodeInterface(self.nodeManager, self.tcpCommander, self.accountPool, self)
        self.accountInterface = AccountInterface(self.accountPool, self)
        self.historyInterface = HistoryInterface(self.nodeManager, self)
        self.logInterface = LogInterface(self)
        self.settingInterface = SettingInterface(self)

        self._initWindow()
        self._initNavigation()

        # 启动 UDP 监听
        self.udpListener.start()

        # 超时检查定时器
        self._timeoutTimer = QTimer(self)
        self._timeoutTimer.timeout.connect(self.nodeManager.check_timeouts)
        self._timeoutTimer.start(5000)

    def _initNavigation(self):
        self.addSubInterface(self.dashboardInterface, FIF.HOME, "仪表盘")
        self.addSubInterface(self.nodeInterface, FIF.IOT, "节点管理")
        self.addSubInterface(self.accountInterface, FIF.PEOPLE, "账号管理")
        self.addSubInterface(self.historyInterface, FIF.HISTORY, "操作历史")
        self.addSubInterface(self.logInterface, FIF.DOCUMENT, "实时日志")
        self.addSubInterface(
            self.settingInterface, FIF.SETTING, "设置",
            NavigationItemPosition.BOTTOM,
        )

    def _initWindow(self):
        self.resize(1200, 800)
        self.setMinimumWidth(900)
        self.setWindowTitle("TriangleAlpha 群控中心")

        if sys.platform != "darwin":
            self.navigationInterface.setAcrylicEnabled(True)
        if sys.platform == "darwin":
            self.setMicaEffectEnabled(False)
            self._fixMacOSTitleBar()

        # 居中显示
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.width() // 2 - self.width() // 2,
                      geo.height() // 2 - self.height() // 2)

    def closeEvent(self, e):
        self.udpListener.stop()
        super().closeEvent(e)

    def _fixMacOSTitleBar(self) -> None:
        self.setSystemTitleBarButtonVisible(True)
        self.titleBar.minBtn.hide()
        self.titleBar.maxBtn.hide()
        self.titleBar.closeBtn.hide()
```

- [ ] **Step 4: 创建入口点**

```python
# src/master/main.py
"""中控端入口"""
import sys
from PyQt6.QtWidgets import QApplication
from master.app.view.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 运行验证窗口能打开**

```bash
cd /Users/daoji/Code/DeltaForce/TriangleAlpha-Master
PYTHONPATH=src uv run python -m master.main
```
Expected: Fluent Design 窗口打开，侧边栏导航可切换，页面显示占位文字。

- [ ] **Step 6: Commit**

```bash
git add src/master/
git commit -m "✨ feat(master): 添加主窗口、导航、占位页面，可启动运行"
```

---

### Task 9: 节点管理页面（核心 UI）

**Files:**
- Modify: `src/master/app/view/node_interface.py`

- [ ] **Step 1: 实现完整的 NodeInterface**

```python
# src/master/app/view/node_interface.py
"""节点管理页面 — 核心 UI"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QHeaderView,
    QTableWidgetItem, QAbstractItemView, QFileDialog,
)
from qfluentwidgets import (
    ScrollArea, SearchLineEdit, ComboBox, PrimaryPushButton,
    PushButton, TableWidget, InfoBar, InfoBarPosition,
    RoundMenu, Action, FluentIcon as FIF, MenuAnimationType,
)

from common.protocol import TcpCommand
from master.app.components.stat_card import StatCard
from master.app.core.node_manager import NodeManager
from master.app.core.tcp_commander import TcpCommander
from master.app.core.account_pool import AccountPool

_HEADERS = ["状态", "机器名", "IP", "分组", "等级", "金币", "当前账号", "CPU%", "内存%", "最后心跳"]


class NodeInterface(ScrollArea):
    def __init__(self, node_mgr: NodeManager, tcp_cmd: TcpCommander,
                 account_pool: AccountPool, parent=None):
        super().__init__(parent)
        self.setObjectName("nodeInterface")
        self._nm = node_mgr
        self._tcp = tcp_cmd
        self._pool = account_pool

        self.view = QWidget(self)
        self.mainLayout = QVBoxLayout(self.view)
        self.mainLayout.setContentsMargins(24, 24, 24, 24)
        self.mainLayout.setSpacing(16)

        # ── 统计卡片 ──
        statsLayout = QHBoxLayout()
        statsLayout.setSpacing(12)
        self.onlineCard = StatCard("在线节点", "0")
        self.totalCard = StatCard("总节点", "0")
        self.accountCard = StatCard("可用账号", "0")
        for card in (self.onlineCard, self.totalCard, self.accountCard):
            statsLayout.addWidget(card)
        self.mainLayout.addLayout(statsLayout)

        # ── 工具栏 ──
        toolLayout = QHBoxLayout()
        self.searchBox = SearchLineEdit(self)
        self.searchBox.setPlaceholderText("搜索机器名/IP...")
        self.searchBox.setFixedWidth(250)
        toolLayout.addWidget(self.searchBox)

        self.groupCombo = ComboBox(self)
        self.groupCombo.addItem("全部")
        self.groupCombo.setFixedWidth(120)
        toolLayout.addWidget(self.groupCombo)

        toolLayout.addStretch()

        self.btnStart = PrimaryPushButton(FIF.PLAY, "启动选中", self)
        self.btnStop = PushButton(FIF.CLOSE, "停止选中", self)
        self.btnReboot = PushButton(FIF.SYNC, "重启脚本", self)
        self.btnRebootPC = PushButton(FIF.POWER_BUTTON, "重启电脑", self)
        self.btnDistKeys = PushButton(FIF.SEND, "分发卡密", self)
        self.btnSendFile = PushButton(FIF.FOLDER, "发送文件", self)
        for btn in (self.btnStart, self.btnStop, self.btnReboot,
                    self.btnRebootPC, self.btnDistKeys, self.btnSendFile):
            toolLayout.addWidget(btn)
        self.mainLayout.addLayout(toolLayout)

        # ── 表格 ──
        self.table = TableWidget(self)
        self.table.setColumnCount(len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._showContextMenu)
        self.mainLayout.addWidget(self.table)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ── 信号连接 ──
        self._nm.node_online.connect(self._onNodeOnline)
        self._nm.node_updated.connect(self._onNodeUpdated)
        self._nm.node_offline.connect(self._onNodeUpdated)
        self._nm.stats_changed.connect(self._refreshStats)
        self._pool.pool_changed.connect(self._refreshStats)

        self.searchBox.textChanged.connect(self._applyFilter)
        self.groupCombo.currentTextChanged.connect(self._applyFilter)

        self.btnStart.clicked.connect(lambda: self._sendToSelected(TcpCommand.START_EXE))
        self.btnStop.clicked.connect(lambda: self._sendToSelected(TcpCommand.STOP_EXE))
        self.btnReboot.clicked.connect(lambda: self._sendToSelected(TcpCommand.START_EXE))
        self.btnRebootPC.clicked.connect(lambda: self._sendToSelected(TcpCommand.REBOOT_PC))
        self.btnDistKeys.clicked.connect(self._distributeKeys)
        self.btnSendFile.clicked.connect(self._sendFile)

        self._tcp.command_sent.connect(self._onCmdSent)
        self._tcp.command_failed.connect(self._onCmdFailed)

        # 行映射: machine_name → row index
        self._row_map: dict[str, int] = {}

    # ── 节点表格更新 ──

    def _onNodeOnline(self, name: str):
        node = self._nm.nodes[name]
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._row_map[name] = row
        self._setRowData(row, node)
        self._refreshGroups()
        InfoBar.success("节点上线", f"{name} ({node.ip})", parent=self,
                        position=InfoBarPosition.TOP, duration=2000)

    def _onNodeUpdated(self, name: str):
        if name not in self._row_map:
            return
        node = self._nm.nodes.get(name)
        if node:
            self._setRowData(self._row_map[name], node)

    def _setRowData(self, row: int, node):
        status_icon = {"在线": "🟢", "离线": "🔴", "断线": "⚫"}.get(node.status, "🟡")
        items = [
            status_icon, node.machine_name, node.ip, node.group,
            str(node.level), node.jin_bi, node.current_account,
            f"{node.cpu_percent:.0f}%", f"{node.mem_percent:.0f}%",
            node.last_seen.strftime("%H:%M:%S"),
        ]
        for col, text in enumerate(items):
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem(text)
                self.table.setItem(row, col, item)
            else:
                item.setText(text)

    def _refreshStats(self):
        self.onlineCard.setValue(f"{self._nm.online_count}")
        self.totalCard.setValue(f"{self._nm.total_count}")
        self.accountCard.setValue(f"{self._pool.available_count}")

    def _refreshGroups(self):
        current = self.groupCombo.currentText()
        self.groupCombo.clear()
        self.groupCombo.addItem("全部")
        for g in self._nm.groups:
            self.groupCombo.addItem(g)
        idx = self.groupCombo.findText(current)
        if idx >= 0:
            self.groupCombo.setCurrentIndex(idx)

    # ── 筛选 ──

    def _applyFilter(self):
        search = self.searchBox.text().lower()
        group = self.groupCombo.currentText()
        for name, row in self._row_map.items():
            node = self._nm.nodes.get(name)
            if not node:
                continue
            match_search = not search or search in name.lower() or search in node.ip
            match_group = group == "全部" or node.group == group
            self.table.setRowHidden(row, not (match_search and match_group))

    # ── 操作 ──

    def _getSelectedIPs(self) -> list[tuple[str, str]]:
        rows = set(idx.row() for idx in self.table.selectedIndexes())
        result = []
        for name, row in self._row_map.items():
            if row in rows:
                node = self._nm.nodes.get(name)
                if node:
                    result.append((name, node.ip))
        return result

    def _sendToSelected(self, cmd: TcpCommand):
        selected = self._getSelectedIPs()
        if not selected:
            InfoBar.warning("提示", "请先选择节点", parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        for name, ip in selected:
            self._tcp.send(ip, cmd)
            self._nm.add_history(cmd.value, name)

    def _distributeKeys(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 key.txt", "", "Text (*.txt)")
        if not path:
            return
        key = open(path, encoding="utf-8").read().strip()
        for node in self._nm.nodes.values():
            if node.status not in ("离线", "断线"):
                self._tcp.send(node.ip, TcpCommand.UPDATE_KEY, key)
        self._nm.add_history("分发卡密", "全部在线节点")
        InfoBar.success("卡密已分发", f"已发送到 {self._nm.online_count} 个节点",
                        parent=self, position=InfoBarPosition.TOP, duration=3000)

    def _sendFile(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if not path:
            return
        # 文件传输逻辑在后续 Task 中完善
        InfoBar.info("提示", f"文件传输功能待完善: {path}", parent=self,
                     position=InfoBarPosition.TOP, duration=3000)

    # ── 右键菜单 ──

    def _showContextMenu(self, pos):
        menu = RoundMenu(parent=self.table)
        menu.addAction(Action(FIF.PLAY, "启动脚本", triggered=lambda: self._sendToSelected(TcpCommand.START_EXE)))
        menu.addAction(Action(FIF.CLOSE, "停止脚本", triggered=lambda: self._sendToSelected(TcpCommand.STOP_EXE)))
        menu.addSeparator()
        menu.addAction(Action(FIF.SYNC, "重启脚本", triggered=lambda: self._sendToSelected(TcpCommand.START_EXE)))
        menu.addAction(Action(FIF.POWER_BUTTON, "重启电脑", triggered=lambda: self._sendToSelected(TcpCommand.REBOOT_PC)))
        menu.exec(self.table.viewport().mapToGlobal(pos), aniType=MenuAnimationType.NONE)

    # ── TCP 回调 ──

    def _onCmdSent(self, ip: str, cmd: str):
        pass  # 可选：更新节点状态

    def _onCmdFailed(self, ip: str, error: str):
        InfoBar.error("通信失败", f"{ip}: {error}", parent=self,
                      position=InfoBarPosition.TOP, duration=5000)
```

- [ ] **Step 2: 运行验证 UI**

```bash
cd /Users/daoji/Code/DeltaForce/TriangleAlpha-Master
PYTHONPATH=src uv run python -m master.main
```
Expected: 节点管理页面显示统计卡片、搜索框、按钮、空表格。

- [ ] **Step 3: Commit**

```bash
git add src/master/app/view/node_interface.py src/master/app/components/stat_card.py
git commit -m "✨ feat(master): 实现节点管理页面（表格+工具栏+右键菜单）"
```

---

## Phase 4: 被控端重写

### Task 10: 被控端核心

**Files:**
- Create: `src/slave/main.py`
- Create: `src/slave/heartbeat.py`
- Create: `src/slave/command_handler.py`
- Create: `src/slave/process_manager.py`

- [ ] **Step 1: 实现心跳服务**

```python
# src/slave/heartbeat.py
"""UDP 心跳广播"""
from __future__ import annotations

import asyncio
import platform
import os
import socket

import psutil

from common.protocol import UDP_PORT, HEARTBEAT_INTERVAL, build_udp_online, build_udp_ext_online

SLAVE_VERSION = "2.0.0"


class HeartbeatService:
    def __init__(self, master_ip: str | None = None, port: int = UDP_PORT,
                 interval: int = HEARTBEAT_INTERVAL):
        self._master_ip = master_ip
        self._port = port
        self._interval = interval
        self._machine_name = platform.node()
        self._user_name = os.getenv("USERNAME", os.getenv("USER", "unknown"))
        self._group = "默认"
        self._running = False

    def set_group(self, group: str) -> None:
        self._group = group

    async def run(self) -> None:
        self._running = True
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)

        loop = asyncio.get_event_loop()

        while self._running:
            try:
                cpu = psutil.cpu_percent(interval=0)
                mem = psutil.virtual_memory().percent
                msg = build_udp_ext_online(
                    self._machine_name, self._user_name,
                    cpu, mem, SLAVE_VERSION, self._group,
                )
                data = msg.encode("utf-8")

                if self._master_ip:
                    target = (self._master_ip, self._port)
                else:
                    target = ("255.255.255.255", self._port)

                await loop.sock_sendto(sock, data, target)
            except Exception:
                pass
            await asyncio.sleep(self._interval)

        sock.close()

    def stop(self) -> None:
        self._running = False
```

- [ ] **Step 2: 实现指令处理器**

```python
# src/slave/command_handler.py
"""TCP 指令接收与处理"""
from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

from slave.process_manager import ProcessManager


class CommandHandler:
    def __init__(self, base_dir: str, port: int = 9999):
        self._base_dir = Path(base_dir)
        self._port = port
        self._pm = ProcessManager(base_dir)

    async def run(self) -> None:
        server = await asyncio.start_server(self._handle_client, "0.0.0.0", self._port)
        print(f"[TCP] 监听端口 {self._port}")
        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not line:
                return
            text = line.decode("utf-8", errors="ignore").strip()
            await self._dispatch(text, reader)
        except Exception as e:
            print(f"[TCP 异常] {e}")
        finally:
            writer.close()

    async def _dispatch(self, text: str, reader: asyncio.StreamReader):
        if text.startswith("UPDATETXT|"):
            payload = text[len("UPDATETXT|"):]
            content = base64.b64decode(payload).decode("utf-8")
            (self._base_dir / "accounts.txt").write_text(content, encoding="utf-8")
            print("[接收] 账号已更新")

        elif text.startswith("UPDATEKEY|"):
            payload = text[len("UPDATEKEY|"):]
            key = base64.b64decode(payload).decode("utf-8")
            (self._base_dir / "key.txt").write_text(key, encoding="utf-8")
            print("[接收] Key 已更新")

        elif text.startswith("STARTEXE|"):
            print("[指令] 启动脚本")
            await self._pm.start_testdemo()

        elif text.startswith("STOPEXE|"):
            print("[指令] 停止脚本")
            await self._pm.stop_all()

        elif text.startswith("REBOOTPC|"):
            print("[指令] 重启电脑")
            os.system("shutdown -r -t 0" if os.name == "nt" else "sudo reboot")

        elif text.startswith("DELETEFILE|"):
            parts = text.split("|")[1:]
            for fname in parts:
                fpath = self._base_dir / fname.strip()
                if fpath.exists():
                    fpath.unlink()
                    print(f"[删除] {fname}")

        elif text.startswith("EXT_SETGROUP|"):
            group = text[len("EXT_SETGROUP|"):]
            print(f"[分组] 设为 {group}")
            # 由 main.py 处理分组变更
```

- [ ] **Step 3: 实现进程管理器**

```python
# src/slave/process_manager.py
"""进程管理（启动/杀死 TestDemo 等）"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import psutil

_KILL_TARGETS = [
    "TestDemo", "steam", "steamwebhelper", "steamerrorreporter",
    "DeltaForce", "Client-Win64-Shipping", "df_launcher",
    "SteamService", "DeltaForceLauncher", "DeltaForceClient",
    "DeltaForceClient-Win64-Shipping",
]


class ProcessManager:
    def __init__(self, base_dir: str):
        self._base_dir = Path(base_dir)

    async def start_testdemo(self) -> None:
        await self.kill_by_name("TestDemo")
        await asyncio.sleep(1)
        exe = self._base_dir / "TestDemo.exe"
        if exe.exists():
            await asyncio.create_subprocess_exec(
                str(exe), cwd=str(self._base_dir),
            )
            print("[启动] TestDemo.exe")

    async def stop_all(self) -> None:
        for name in _KILL_TARGETS:
            await self.kill_by_name(name)
        print("[清理] 所有游戏进程已停止")

    async def kill_by_name(self, name: str) -> int:
        killed = 0
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] and proc.info["name"].lower().startswith(name.lower()):
                    proc.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return killed
```

- [ ] **Step 4: 实现入口点**

```python
# src/slave/main.py
"""被控端入口"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from slave.heartbeat import HeartbeatService
from slave.command_handler import CommandHandler


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _read_master_ip(base_dir: Path) -> str | None:
    for name in ("主控IP.txt", "master.txt"):
        p = base_dir / name
        if p.exists():
            ip = p.read_text(encoding="utf-8").strip()
            if ip:
                return ip
    return None


async def _main():
    base_dir = _get_base_dir()
    master_ip = _read_master_ip(base_dir)
    print(f"[启动] 被控端 v2.0.0")
    print(f"[目录] {base_dir}")
    print(f"[主控] {master_ip or '广播模式'}")

    heartbeat = HeartbeatService(master_ip=master_ip)
    handler = CommandHandler(str(base_dir))

    await asyncio.gather(
        heartbeat.run(),
        handler.run(),
    )


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Commit**

```bash
git add src/slave/
git commit -m "✨ feat(slave): 重写被控端（心跳+指令处理+进程管理）"
```

---

## Phase 5: 补全剩余 UI 页面

### Task 11: 仪表盘页面

**Files:**
- Modify: `src/master/app/view/dashboard_interface.py`

- [ ] **Step 1: 实现仪表盘**

替换占位内容，实现 4 个 StatCard + 最近操作列表。从 NodeManager 和 AccountPool 读取数据，通过信号实时更新。

- [ ] **Step 2: Commit**

### Task 12: 账号管理页面

**Files:**
- Modify: `src/master/app/view/account_interface.py`

- [ ] **Step 1: 实现账号管理**

导入按钮 + 账号表格（掩码密码）+ 统计 + 导出。连接 AccountPool 信号。

- [ ] **Step 2: Commit**

### Task 13: 操作历史页面

**Files:**
- Modify: `src/master/app/view/history_interface.py`

- [ ] **Step 1: 实现操作历史**

读取 NodeManager.history，表格显示。支持按类型筛选。

- [ ] **Step 2: Commit**

### Task 14: 实时日志页面

**Files:**
- Modify: `src/master/app/view/log_interface.py`
- Create: `src/master/app/core/log_receiver.py`
- Create: `src/slave/log_reporter.py`

- [ ] **Step 1: 实现日志接收线程（中控端）和日志上报（被控端）**
- [ ] **Step 2: 实现日志 UI（左节点列表+右日志流）**
- [ ] **Step 3: Commit**

### Task 15: 设置页面

**Files:**
- Modify: `src/master/app/view/setting_interface.py`

- [ ] **Step 1: 实现设置页面**

使用 SettingCardGroup：网络配置、外观（主题/主题色）、关于。

- [ ] **Step 2: Commit**

---

## Phase 6: 集成测试 + 收尾

### Task 16: 全量测试

- [ ] **Step 1: 运行全部测试**

```bash
uv run pytest tests/ -v
```

- [ ] **Step 2: Ruff + Mypy 检查**

```bash
uv run ruff check src/
uv run mypy src/common/ src/master/app/core/
```

- [ ] **Step 3: 修复所有问题**

- [ ] **Step 4: 端到端验证**

在 macOS 上启动 master，在 VM100 上启动 slave（或本地起两个进程），验证：
1. 节点自动出现在列表
2. 启动/停止指令正常发送
3. 账号分发正常
4. 统计数据实时更新

- [ ] **Step 5: 最终 Commit**

```bash
git add -A
git commit -m "✅ test: 全量测试通过，v0.1.0 基础功能完成"
```
