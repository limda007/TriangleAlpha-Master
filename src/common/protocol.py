"""通信协议：消息解析、构建、常量定义"""
from __future__ import annotations

import base64
import enum
import hashlib
import logging
import math
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def _safe_float(s: str) -> float:
    try:
        v = float(s)
        return v if math.isfinite(v) else 0.0
    except (ValueError, TypeError):
        return 0.0

UDP_PORT = 8888
TCP_CMD_PORT = 9999
TCP_LOG_PORT = 8890
HEARTBEAT_INTERVAL = 3
LOCAL_IPC_PORT = 8889       # TestDemo → slave 本地 IPC 端口
IPC_TIMEOUT = 15            # IPC 数据过期阈值（秒）
OFFLINE_TIMEOUT = 15
DISCONNECT_TIMEOUT = 60
TCP_SEND_TIMEOUT = 60
PROTOCOL_VERSION = "1"
SLAVE_SELF_UPDATE_FILENAME = "TriangleAlpha-Slave.exe"
ACCOUNT_RUNTIME_CLEANUP_FILES = (
    "accounts.txt.imported",
    "accounts.json",
    "configs/accounts.json",
    "accounts.txt",
    "configs/accounts.txt",
    "runtime_status.json",
    "runtime/runtime_status.json",
    "runtime/agent_status.json",
)
ACCOUNT_RUNTIME_CLEANUP_PAYLOAD = "|".join(ACCOUNT_RUNTIME_CLEANUP_FILES)


class UdpMessageType(enum.Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    STATUS = "STATUS"
    EXT_ONLINE = "EXT_ONLINE"
    ACCOUNT_SYNC = "ACCOUNT_SYNC"
    NEED_ACCOUNT = "NEED_ACCOUNT"


class GameState:
    """TestDemo.exe STATUS 消息的 state 字段约定值"""
    COMPLETED = "已完成"
    RUNNING = "运行中"
    SCRIPT_STOPPED = "脚本已停止"  # slave 检测到 TestDemo 停止时上报

    # TestDemo 可能上报数字状态码，统一映射为中文
    _CODE_MAP = {
        "1": "运行中",
        "2": "已完成",
        "3": "脚本已停止",
    }
    # 白名单：仅接受已知状态值（防止 TestDemo 直发 STATUS 的账号名污染）
    _KNOWN = {COMPLETED, RUNNING, SCRIPT_STOPPED}

    @classmethod
    def normalize(cls, raw: str) -> str:
        """将原始状态值（数字或中文）统一为中文显示，未知值一律丢弃"""
        match raw:
            case "" | None:
                return ""
            case v if v in cls._CODE_MAP:
                return cls._CODE_MAP[v]
            case v if v in cls._KNOWN:
                return v
            case _:
                return ""


@dataclass
class UdpMessage:
    type: UdpMessageType
    machine_name: str = ""
    user_name: str = ""
    state: str = ""
    level: int = 0
    jin_bi: str = "0"
    desc: str = ""
    elapsed: str = "0"  # 运行时间（分钟或秒，由 TestDemo 上报）
    status_text: str = ""  # IPC 原始状态文本（如 "等待匹配"/"正在对局"）
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    slave_version: str = ""
    group: str = "默认"
    teammate_fill: str = ""
    weapon_config: str = ""
    level_threshold: str = ""
    loot_count: str = ""
    sync_payload: str = ""  # ACCOUNT_SYNC: base64 编码的 JSON 账号数组
    token_key: str = ""     # EXT_ONLINE: slave 端 token.txt 内容
    kami_code: str = ""     # EXT_ONLINE: slave 端 kamis.txt 当前值
    vram_used_mb: int = 0   # GPU 显存已用 (MB)
    vram_total_mb: int = 0  # GPU 显存总量 (MB)
    client_type: str = "slave"
    agent_version: str = ""
    protocol_version: str = ""


def parse_udp_message(raw: str) -> UdpMessage | None:
    parts = raw.split("|")
    if not parts:
        return None
    match parts[0]:
        case "ONLINE" if len(parts) >= 3:
            return UdpMessage(type=UdpMessageType.ONLINE, machine_name=parts[1], user_name=parts[2])
        case "OFFLINE" if len(parts) >= 2:
            return UdpMessage(type=UdpMessageType.OFFLINE, machine_name=parts[1])
        case "STATUS" if len(parts) >= 6:
            return UdpMessage(
                type=UdpMessageType.STATUS,
                machine_name=parts[1],
                state=parts[2],
                level=int(parts[3]) if parts[3].isdigit() else 0,
                jin_bi=parts[4],
                desc=parts[5],
                elapsed=parts[6] if len(parts) >= 7 else "0",
                status_text=parts[7] if len(parts) >= 8 else "",
            )
        case "EXT_ONLINE" if len(parts) >= 7:
            return UdpMessage(
                type=UdpMessageType.EXT_ONLINE,
                machine_name=parts[1],
                user_name=parts[2],
                cpu_percent=_safe_float(parts[3]),
                mem_percent=_safe_float(parts[4]),
                slave_version=parts[5],
                group=parts[6],
                teammate_fill=parts[7] if len(parts) >= 8 else "",
                weapon_config=parts[8] if len(parts) >= 9 else "",
                level_threshold=parts[9] if len(parts) >= 10 else "",
                loot_count=parts[10] if len(parts) >= 11 else "",
                token_key=parts[11] if len(parts) >= 12 else "",
                kami_code=parts[12] if len(parts) >= 13 else "",
                vram_used_mb=int(parts[13]) if len(parts) >= 14 and parts[13].isdigit() else 0,
                vram_total_mb=int(parts[14]) if len(parts) >= 15 and parts[14].isdigit() else 0,
                client_type=parts[15] if len(parts) >= 16 and parts[15] else "slave",
                agent_version=parts[16] if len(parts) >= 17 else "",
                protocol_version=parts[17] if len(parts) >= 18 else "",
            )
        case "ACCOUNT_SYNC" if len(parts) >= 3 and parts[2]:
            return UdpMessage(
                type=UdpMessageType.ACCOUNT_SYNC,
                machine_name=parts[1],
                sync_payload=parts[2],
            )
        case "NEED_ACCOUNT" if len(parts) >= 2:
            return UdpMessage(
                type=UdpMessageType.NEED_ACCOUNT,
                machine_name=parts[1],
            )
    logger.debug("未识别的 UDP 消息: %s", parts[0] if parts else "(empty)")
    return None


def build_udp_online(machine_name: str, user_name: str) -> str:  # legacy: 仅测试使用，生产环境由 EXT_ONLINE 替代
    return f"ONLINE|{machine_name}|{user_name}"


def build_udp_ext_online(
    machine_name: str, user_name: str, cpu: float, mem: float,
    version: str, group: str, teammate_fill: str = "",
    weapon_config: str = "", level_threshold: str = "",
    loot_count: str = "", token_key: str = "", kami_code: str = "",
    vram_used_mb: int = 0, vram_total_mb: int = 0,
    client_type: str = "slave", agent_version: str = "",
    protocol_version: str = "",
) -> str:
    msg = (
        f"EXT_ONLINE|{machine_name}|{user_name}|{cpu:.1f}|{mem:.1f}"
        f"|{version}|{group}|{teammate_fill}|{weapon_config}|{level_threshold}|{loot_count}|{token_key}|{kami_code}"
        f"|{vram_used_mb}|{vram_total_mb}"
    )
    if client_type != "slave" or agent_version or protocol_version:
        msg += f"|{client_type}|{agent_version}|{protocol_version}"
    return msg


def build_udp_master_here(
    master_name: str,
    tcp_cmd_port: int = TCP_CMD_PORT,
    tcp_log_port: int = TCP_LOG_PORT,
    protocol_version: str = PROTOCOL_VERSION,
    tenant_id: str = "",
) -> str:
    """构造 ``MASTER_HERE`` wire 文本.

    向后兼容: ``tenant_id`` 为空时退化为 5 段(老 agent 可解析); 非空时附加第 6 段.
    """
    for label, value in (
        ("master_name", master_name),
        ("protocol_version", protocol_version),
        ("tenant_id", tenant_id),
    ):
        if "|" in value or "\n" in value or "\r" in value:
            raise ValueError(f"{label} contains forbidden separator: {value!r}")
    base = f"MASTER_HERE|{master_name}|{tcp_cmd_port}|{tcp_log_port}|{protocol_version}"
    if tenant_id:
        return f"{base}|{tenant_id}"
    return base


def parse_discover_master(raw: str) -> tuple[str, str, str, str] | None:
    """解析 agent 端 ``DISCOVER_MASTER`` wire 文本.

    Returns:
        ``(machine_name, agent_version, protocol_version, tenant_id)`` 或 ``None``.
        旧 agent 4 段(无 tenant) → tenant_id 为空字符串; 新 agent 5 段;
        其它形态一律 ``None``.
    """
    parts = raw.strip().split("|")
    if len(parts) not in (4, 5) or parts[0] != "DISCOVER_MASTER":
        return None
    tenant = parts[4] if len(parts) == 5 else ""
    return (parts[1], parts[2], parts[3], tenant)


def should_reply_to_discovery(
    remote_tenant: str,
    *,
    local_tenant: str,
    strict: bool = True,
) -> bool:
    """租户匹配判定 (与 agent 侧 TenantPolicy 对称).

    机房同 LAN 多客户共存时, master 仅应回复同租户 agent 的发现请求.
    严格模式拒绝任意一端为空的混合配置以防误绑.
    """
    if local_tenant == "" and remote_tenant == "":
        return True
    if local_tenant == "" or remote_tenant == "":
        return not strict
    return local_tenant == remote_tenant


def build_udp_offline(machine_name: str) -> str:
    return f"OFFLINE|{machine_name}"


def build_udp_status(
    machine_name: str, state: str, level: int, jin_bi: str,
    desc: str, elapsed: str = "0", status_text: str = "",
) -> str:
    return f"STATUS|{machine_name}|{state}|{level}|{jin_bi}|{desc}|{elapsed}|{status_text}"


def build_udp_account_sync(machine_name: str, payload_b64: str) -> str:
    return f"ACCOUNT_SYNC|{machine_name}|{payload_b64}"


def build_udp_need_account(machine_name: str) -> str:
    return f"NEED_ACCOUNT|{machine_name}"


class TcpCommand(enum.Enum):
    UPDATE_TXT = "UPDATETXT"
    UPDATE_SELF = "UPDATESELF"
    START_EXE = "STARTEXE"
    STOP_EXE = "STOPEXE"
    REBOOT_PC = "REBOOTPC"
    UPDATE_KEY = "UPDATEKEY"
    DELETE_FILE = "DELETEFILE"
    EXT_SET_GROUP = "EXT_SETGROUP"
    EXT_SET_CONFIG = "EXT_SETCONFIG"
    PUSH_KAMI = "PUSHKAMI"


@dataclass(frozen=True)
class ParsedTcpCommand:
    command: TcpCommand
    payload: str = ""


def build_tcp_command(cmd: TcpCommand, payload: str = "") -> str:
    if cmd in (TcpCommand.UPDATE_TXT, TcpCommand.UPDATE_KEY, TcpCommand.PUSH_KAMI) and payload:
        encoded = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
        return f"{cmd.value}|{encoded}"
    if cmd in (
        TcpCommand.UPDATE_SELF,
        TcpCommand.EXT_SET_GROUP, TcpCommand.DELETE_FILE,
        TcpCommand.EXT_SET_CONFIG,
    ) and payload:
        return f"{cmd.value}|{payload}"
    return f"{cmd.value}|"


def build_self_update_payload(filename: str, raw: bytes) -> str:
    safe_name = Path(filename.strip()).name
    if not safe_name:
        raise ValueError("自更新文件名不能为空")
    content_b64 = base64.b64encode(raw).decode("ascii")
    sha256 = hashlib.sha256(raw).hexdigest()
    return f"{safe_name}|SHA256:{sha256}|SIZE:{len(raw)}|{content_b64}"


def parse_tcp_command(raw: str) -> ParsedTcpCommand | None:
    command_text, has_sep, payload = raw.partition("|")
    try:
        command = TcpCommand(command_text)
    except ValueError:
        return None
    return ParsedTcpCommand(command=command, payload=payload if has_sep else "")
