"""节点状态管理器：处理 UDP 消息，维护节点列表"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from common.models import NodeInfo, OperationRecord
from common.protocol import DISCONNECT_TIMEOUT, OFFLINE_TIMEOUT, UdpMessage, UdpMessageType


class NodeManager(QObject):
    """管理所有被控端节点的状态"""

    # 信号
    node_updated = pyqtSignal(str)   # machine_name — 节点信息更新
    node_online = pyqtSignal(str)    # machine_name — 新节点上线
    node_offline = pyqtSignal(str)   # machine_name — 节点离线
    stats_changed = pyqtSignal()     # 统计数据变化
    history_changed = pyqtSignal()   # 操作历史变化 (M1)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.nodes: dict[str, NodeInfo] = {}
        self.history: list[OperationRecord] = []

    # ── 公开接口 ───────────────────────────────────────────

    def handle_udp_message(self, msg: UdpMessage, remote_ip: str) -> None:
        """根据消息类型分派处理"""
        handlers = {
            UdpMessageType.ONLINE: self._handle_online,
            UdpMessageType.EXT_ONLINE: self._handle_ext_online,
            UdpMessageType.OFFLINE: self._handle_offline,
            UdpMessageType.STATUS: self._handle_status,
        }
        handler = handlers.get(msg.type)
        if handler:
            handler(msg, remote_ip)

    def check_timeouts(self) -> None:
        """检查超时节点，标记离线 / 断连"""
        now = datetime.now()
        changed = False
        for node in self.nodes.values():
            elapsed = (now - node.last_seen).total_seconds()
            if node.status not in ("离线", "断连"):
                if elapsed >= DISCONNECT_TIMEOUT:
                    node.status = "断连"
                    self.node_offline.emit(node.machine_name)
                    changed = True
                elif elapsed >= OFFLINE_TIMEOUT:
                    node.status = "离线"
                    self.node_offline.emit(node.machine_name)
                    changed = True
        if changed:
            self.stats_changed.emit()

    def add_history(self, op_type: str, target: str, detail: str = "", result: str = "") -> None:
        """追加操作记录"""
        record = OperationRecord(
            timestamp=datetime.now(),
            op_type=op_type,
            target=target,
            detail=detail,
            result=result,
        )
        self.history.append(record)
        self.history_changed.emit()

    def get_nodes_by_group(self, group: str) -> list[NodeInfo]:
        """按分组筛选节点"""
        return [n for n in self.nodes.values() if n.group == group]

    # ── 属性 ───────────────────────────────────────────────

    @property
    def online_count(self) -> int:
        return sum(1 for n in self.nodes.values() if n.status not in ("离线", "断连"))

    @property
    def total_count(self) -> int:
        return len(self.nodes)

    @property
    def groups(self) -> list[str]:
        seen: dict[str, None] = {}
        for n in self.nodes.values():
            seen.setdefault(n.group, None)
        return list(seen)

    # ── 内部处理 ───────────────────────────────────────────

    def _handle_online(self, msg: UdpMessage, remote_ip: str) -> None:
        name = msg.machine_name
        is_new = name not in self.nodes
        if is_new:
            self.nodes[name] = NodeInfo(machine_name=name, ip=remote_ip, user_name=msg.user_name)
        else:
            node = self.nodes[name]
            node.ip = remote_ip
            node.user_name = msg.user_name
            node.status = "在线"
            node.last_seen = datetime.now()
        if is_new:
            self.node_online.emit(name)
        self.node_updated.emit(name)
        self.stats_changed.emit()

    def _handle_ext_online(self, msg: UdpMessage, remote_ip: str) -> None:
        name = msg.machine_name
        is_new = name not in self.nodes
        if is_new:
            self.nodes[name] = NodeInfo(
                machine_name=name,
                ip=remote_ip,
                user_name=msg.user_name,
                group=msg.group,
                cpu_percent=msg.cpu_percent,
                mem_percent=msg.mem_percent,
                slave_version=msg.slave_version,
            )
        else:
            node = self.nodes[name]
            node.ip = remote_ip
            node.user_name = msg.user_name
            node.status = "在线"
            node.group = msg.group
            node.cpu_percent = msg.cpu_percent
            node.mem_percent = msg.mem_percent
            node.slave_version = msg.slave_version
            node.last_seen = datetime.now()
        if is_new:
            self.node_online.emit(name)
        self.node_updated.emit(name)
        self.stats_changed.emit()

    def _handle_offline(self, msg: UdpMessage, _remote_ip: str) -> None:
        name = msg.machine_name
        if name in self.nodes:
            self.nodes[name].status = "离线"
            self.node_offline.emit(name)
            self.node_updated.emit(name)
            self.stats_changed.emit()

    def _handle_status(self, msg: UdpMessage, remote_ip: str) -> None:
        name = msg.machine_name
        if name not in self.nodes:
            # 收到状态消息但节点不存在，先创建
            self.nodes[name] = NodeInfo(machine_name=name, ip=remote_ip)
            self.node_online.emit(name)
        node = self.nodes[name]
        node.status = msg.state if msg.state else node.status
        node.level = msg.level
        node.jin_bi = msg.jin_bi
        node.last_seen = datetime.now()
        node.last_status_update = datetime.now()
        self.node_updated.emit(name)
        self.stats_changed.emit()
