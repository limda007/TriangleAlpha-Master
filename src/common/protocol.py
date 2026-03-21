"""通信协议：消息解析、构建、常量定义"""
from __future__ import annotations

import base64
import enum
from dataclasses import dataclass

UDP_PORT = 8888
TCP_CMD_PORT = 9999
TCP_LOG_PORT = 8890
HEARTBEAT_INTERVAL = 3
LOCAL_IPC_PORT = 8889       # TestDemo → slave 本地 IPC 端口
IPC_TIMEOUT = 15            # IPC 数据过期阈值（秒）
OFFLINE_TIMEOUT = 15
DISCONNECT_TIMEOUT = 60
TCP_SEND_TIMEOUT = 10


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

    @classmethod
    def normalize(cls, raw: str) -> str:
        """将原始状态值（数字或中文）统一为中文显示，纯数字视为无效状态"""
        match raw:
            case "" | None:
                return ""
            case v if v in cls._CODE_MAP:
                return cls._CODE_MAP[v]
            case v if v.isdigit():
                # 纯数字不是有效状态（可能是账号号码等误传）
                return ""
            case _:
                return raw


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
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    slave_version: str = ""
    group: str = "默认"
    teammate_fill: str = ""
    weapon_config: str = ""
    level_threshold: str = ""
    loot_count: str = ""
    sync_payload: str = ""  # ACCOUNT_SYNC: base64 编码的 JSON 账号数组


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
            )
        case "EXT_ONLINE" if len(parts) >= 7:
            return UdpMessage(
                type=UdpMessageType.EXT_ONLINE,
                machine_name=parts[1],
                user_name=parts[2],
                cpu_percent=float(parts[3]) if parts[3].replace(".", "").isdigit() else 0.0,
                mem_percent=float(parts[4]) if parts[4].replace(".", "").isdigit() else 0.0,
                slave_version=parts[5],
                group=parts[6],
                teammate_fill=parts[7] if len(parts) >= 8 else "",
                weapon_config=parts[8] if len(parts) >= 9 else "",
                level_threshold=parts[9] if len(parts) >= 10 else "",
                loot_count=parts[10] if len(parts) >= 11 else "",
            )
        case "ACCOUNT_SYNC" if len(parts) >= 3:
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
    return None


def build_udp_online(machine_name: str, user_name: str) -> str:  # legacy: 仅测试使用，生产环境由 EXT_ONLINE 替代
    return f"ONLINE|{machine_name}|{user_name}"


def build_udp_ext_online(
    machine_name: str, user_name: str, cpu: float, mem: float,
    version: str, group: str, teammate_fill: str = "",
    weapon_config: str = "", level_threshold: str = "",
    loot_count: str = "",
) -> str:
    return (
        f"EXT_ONLINE|{machine_name}|{user_name}|{cpu:.1f}|{mem:.1f}"
        f"|{version}|{group}|{teammate_fill}|{weapon_config}|{level_threshold}|{loot_count}"
    )


def build_udp_offline(machine_name: str) -> str:
    return f"OFFLINE|{machine_name}"


def build_udp_status(machine_name: str, state: str, level: int, jin_bi: str, desc: str, elapsed: str = "0") -> str:
    return f"STATUS|{machine_name}|{state}|{level}|{jin_bi}|{desc}|{elapsed}"


def build_udp_account_sync(machine_name: str, payload_b64: str) -> str:
    return f"ACCOUNT_SYNC|{machine_name}|{payload_b64}"


class TcpCommand(enum.Enum):
    UPDATE_TXT = "UPDATETXT"
    START_EXE = "STARTEXE"
    STOP_EXE = "STOPEXE"
    REBOOT_PC = "REBOOTPC"
    UPDATE_KEY = "UPDATEKEY"
    DELETE_FILE = "DELETEFILE"
    EXT_SET_GROUP = "EXT_SETGROUP"
    EXT_SET_CONFIG = "EXT_SETCONFIG"


@dataclass(frozen=True)
class ParsedTcpCommand:
    command: TcpCommand
    payload: str = ""


def build_tcp_command(cmd: TcpCommand, payload: str = "") -> str:
    if cmd in (TcpCommand.UPDATE_TXT, TcpCommand.UPDATE_KEY) and payload:
        encoded = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
        return f"{cmd.value}|{encoded}"
    elif cmd in (
        TcpCommand.EXT_SET_GROUP, TcpCommand.DELETE_FILE,
        TcpCommand.EXT_SET_CONFIG,
    ) and payload:
        return f"{cmd.value}|{payload}"
    else:
        return f"{cmd.value}|"


def parse_tcp_command(raw: str) -> ParsedTcpCommand | None:
    command_text, has_sep, payload = raw.partition("|")
    try:
        command = TcpCommand(command_text)
    except ValueError:
        return None
    return ParsedTcpCommand(command=command, payload=payload if has_sep else "")
