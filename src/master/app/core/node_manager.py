"""节点状态管理器：处理 UDP 消息，维护节点列表"""
from __future__ import annotations

import base64
import json
from datetime import datetime

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from common.models import NodeInfo, OperationRecord
from common.protocol import DISCONNECT_TIMEOUT, OFFLINE_TIMEOUT, GameState, UdpMessage, UdpMessageType

_MAX_HISTORY = 1000
_STALE_NODE_HOURS = 24  # 断连超过此小时数的节点将被清理


class NodeManager(QObject):
    """管理所有被控端节点的状态"""

    # 信号
    node_updated = pyqtSignal(str)   # machine_name — 节点信息更新
    node_online = pyqtSignal(str)    # machine_name — 新节点上线
    node_offline = pyqtSignal(str)   # machine_name — 节点离线
    node_status_reported = pyqtSignal(str)  # machine_name — 仅 STATUS 消息触发
    account_sync_received = pyqtSignal(str, object)  # (machine_name, list[dict]) — 账号同步
    need_account = pyqtSignal(str)  # machine_name — TestDemo 请求新账号
    stats_changed = pyqtSignal()     # 统计数据变化
    history_changed = pyqtSignal()   # 操作历史变化 (M1)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.nodes: dict[str, NodeInfo] = {}
        self.history: list[OperationRecord] = []
        # P0: 缓存在线/总数，避免每次遍历
        self._online_count = 0
        self._total_count = 0
        # P0: stats_changed 信号防抖 200ms，合并批量 STATUS 消息
        self._stats_dirty = False
        self._stats_timer = QTimer(self)
        self._stats_timer.setSingleShot(True)
        self._stats_timer.setInterval(200)
        self._stats_timer.timeout.connect(self._flush_stats)

    # ── 公开接口 ───────────────────────────────────────────

    def handle_udp_message(self, msg: UdpMessage, remote_ip: str) -> None:
        """根据消息类型分派处理"""
        match msg.type:
            case UdpMessageType.ONLINE:
                self._handle_online(msg, remote_ip)
            case UdpMessageType.EXT_ONLINE:
                self._handle_ext_online(msg, remote_ip)
            case UdpMessageType.OFFLINE:
                self._handle_offline(msg, remote_ip)
            case UdpMessageType.STATUS:
                self._handle_status(msg, remote_ip)
            case UdpMessageType.ACCOUNT_SYNC:
                self._handle_account_sync(msg)
            case UdpMessageType.NEED_ACCOUNT:
                self.need_account.emit(msg.machine_name)

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
            self._recalc_online()
            self._schedule_stats()

    def purge_stale_nodes(self) -> int:
        """清理断连超过 _STALE_NODE_HOURS 的节点，释放内存"""
        now = datetime.now()
        stale = [
            name for name, node in self.nodes.items()
            if node.status in ("离线", "断连")
            and (now - node.last_seen).total_seconds() > _STALE_NODE_HOURS * 3600
        ]
        for name in stale:
            del self.nodes[name]
        if stale:
            self._recalc_online()
            self._schedule_stats()
        return len(stale)

    def add_history(self, op_type: str, target: str, detail: str = "", result: str = "") -> None:
        """追加操作记录（上限 _MAX_HISTORY 条，超出丢弃最旧的）"""
        record = OperationRecord(
            timestamp=datetime.now(),
            op_type=op_type,
            target=target,
            detail=detail,
            result=result,
        )
        self.history.append(record)
        if len(self.history) > _MAX_HISTORY:
            self.history = self.history[-_MAX_HISTORY:]
        self.history_changed.emit()

    def get_nodes_by_group(self, group: str) -> list[NodeInfo]:
        """按分组筛选节点"""
        return [n for n in self.nodes.values() if n.group == group]

    # ── 属性 ───────────────────────────────────────────────

    @property
    def online_count(self) -> int:
        return self._online_count

    @property
    def total_count(self) -> int:
        return self._total_count

    def _recalc_online(self) -> None:
        """重新计算在线数（仅在节点上下线时调用）"""
        self._online_count = sum(1 for n in self.nodes.values() if n.status not in ("离线", "断连"))
        self._total_count = len(self.nodes)

    def _schedule_stats(self) -> None:
        """标记统计脏位，QTimer 200ms 防抖后批量发射 stats_changed"""
        self._stats_dirty = True
        if not self._stats_timer.isActive():
            self._stats_timer.start()

    def _flush_stats(self) -> None:
        """防抖触发：发射 stats_changed 信号"""
        if self._stats_dirty:
            self._stats_dirty = False
            self.stats_changed.emit()

    @property
    def groups(self) -> list[str]:
        seen: dict[str, None] = {}
        for n in self.nodes.values():
            seen.setdefault(n.group, None)
        return list(seen)

    # ── 内部处理 ───────────────────────────────────────────

    def _upsert_node(self, msg: UdpMessage, remote_ip: str, **extra: object) -> None:
        """创建或更新节点，发射信号。extra 为 EXT_ONLINE 的扩展字段。"""
        name = msg.machine_name
        is_new = name not in self.nodes
        if is_new:
            self.nodes[name] = NodeInfo(
                machine_name=name, ip=remote_ip, user_name=msg.user_name, **extra,
            )
        else:
            node = self.nodes[name]
            node.ip = remote_ip
            node.user_name = msg.user_name
            node.status = "在线"
            node.last_seen = datetime.now()
            for attr, value in extra.items():
                setattr(node, attr, value)
        if is_new:
            self.node_online.emit(name)
        self.node_updated.emit(name)
        self._recalc_online()
        self._schedule_stats()

    def _handle_online(self, msg: UdpMessage, remote_ip: str) -> None:
        self._upsert_node(msg, remote_ip)

    def _handle_ext_online(self, msg: UdpMessage, remote_ip: str) -> None:
        self._upsert_node(
            msg, remote_ip,
            group=msg.group,
            cpu_percent=msg.cpu_percent,
            mem_percent=msg.mem_percent,
            slave_version=msg.slave_version,
            teammate_fill=msg.teammate_fill,
            weapon_config=msg.weapon_config,
            level_threshold=msg.level_threshold,
            loot_count=msg.loot_count,
            token_key=msg.token_key,
            kami_code=msg.kami_code,
        )

    def _handle_offline(self, msg: UdpMessage, _remote_ip: str) -> None:
        name = msg.machine_name
        if name in self.nodes:
            self.nodes[name].status = "离线"
            self.node_offline.emit(name)
            self.node_updated.emit(name)
            self._recalc_online()
            self._schedule_stats()

    def _handle_status(self, msg: UdpMessage, remote_ip: str) -> None:
        name = msg.machine_name
        # TestDemo 直发的 STATUS 格式与 Python slave 不同（字段错位），
        # 表现为 state 是纯数字（实为账号号码），标准化后为空 → 丢弃
        state = GameState.normalize(msg.state)
        if msg.state and not state:
            return
        if name not in self.nodes:
            # 收到状态消息但节点不存在，先创建
            self.nodes[name] = NodeInfo(machine_name=name, ip=remote_ip)
            self.node_online.emit(name)
        node = self.nodes[name]
        # 写入 game_state 而非 status（status 由心跳和超时管理）
        if state == GameState.SCRIPT_STOPPED:
            node.game_state = ""
            node.status_text = ""
            node.elapsed = "0"
            # 保留 level/jin_bi/current_account 作为最后已知值，
            # 避免大屏显示清空；DB 侧由 _syncAccountFromNode 守卫
        else:
            node.game_state = state if state else node.game_state
            node.status_text = msg.status_text if msg.status_text else node.status_text
            if msg.level:
                node.level = msg.level
            if msg.jin_bi and msg.jin_bi != "0":
                node.jin_bi = msg.jin_bi
            if msg.elapsed and msg.elapsed != "0":
                node.elapsed = msg.elapsed
            # desc 字段携带当前挂机的游戏账号名
            if msg.desc:
                node.current_account = msg.desc
        node.last_seen = datetime.now()
        node.last_status_update = datetime.now()
        self.node_updated.emit(name)
        self.node_status_reported.emit(name)
        self._recalc_online()
        self._schedule_stats()

    def _handle_account_sync(self, msg: UdpMessage) -> None:
        """解码 ACCOUNT_SYNC 消息并发射信号给 AccountDB。"""
        if not msg.sync_payload:
            return
        try:
            raw = base64.b64decode(msg.sync_payload).decode("utf-8")
            accounts = json.loads(raw)
        except Exception:
            return
        if isinstance(accounts, list):
            self.account_sync_received.emit(msg.machine_name, accounts)
