"""通信协议：消息解析、构建、常量定义"""
from __future__ import annotations

import base64
import enum
from dataclasses import dataclass

UDP_PORT = 8888
TCP_CMD_PORT = 9999
TCP_LOG_PORT = 8890
HEARTBEAT_INTERVAL = 3
OFFLINE_TIMEOUT = 15
DISCONNECT_TIMEOUT = 60
TCP_SEND_TIMEOUT = 10


class UdpMessageType(enum.Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    STATUS = "STATUS"
    EXT_ONLINE = "EXT_ONLINE"


class GameState:
    """TestDemo.exe STATUS 消息的 state 字段约定值"""
    COMPLETED = "已完成"
    RUNNING = "运行中"
    SCRIPT_STOPPED = "脚本已停止"  # slave 检测到 TestDemo 停止时上报


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


def parse_udp_message(raw: str) -> UdpMessage | None:
    parts = raw.split("|")
    if not parts:
        return None
    t = parts[0]
    if t == "ONLINE" and len(parts) >= 3:
        return UdpMessage(type=UdpMessageType.ONLINE, machine_name=parts[1], user_name=parts[2])
    elif t == "OFFLINE" and len(parts) >= 2:
        return UdpMessage(type=UdpMessageType.OFFLINE, machine_name=parts[1])
    elif t == "STATUS" and len(parts) >= 6:
        return UdpMessage(
            type=UdpMessageType.STATUS,
            machine_name=parts[1],
            state=parts[2],
            level=int(parts[3]) if parts[3].isdigit() else 0,
            jin_bi=parts[4],
            desc=parts[5],
            elapsed=parts[6] if len(parts) >= 7 else "0",
        )
    elif t == "EXT_ONLINE" and len(parts) >= 7:
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


def build_udp_online(machine_name: str, user_name: str) -> str:  # legacy: 仅测试使用，生产环境由 EXT_ONLINE 替代
    return f"ONLINE|{machine_name}|{user_name}"


def build_udp_ext_online(machine_name: str, user_name: str, cpu: float, mem: float, version: str, group: str) -> str:
    return f"EXT_ONLINE|{machine_name}|{user_name}|{cpu:.1f}|{mem:.1f}|{version}|{group}"


def build_udp_offline(machine_name: str) -> str:
    return f"OFFLINE|{machine_name}"


def build_udp_status(machine_name: str, state: str, level: int, jin_bi: str, desc: str, elapsed: str = "0") -> str:
    return f"STATUS|{machine_name}|{state}|{level}|{jin_bi}|{desc}|{elapsed}"


class TcpCommand(enum.Enum):
    UPDATE_TXT = "UPDATETXT"
    START_EXE = "STARTEXE"
    STOP_EXE = "STOPEXE"
    REBOOT_PC = "REBOOTPC"
    UPDATE_KEY = "UPDATEKEY"
    DELETE_FILE = "DELETEFILE"
    EXT_SET_GROUP = "EXT_SETGROUP"


def build_tcp_command(cmd: TcpCommand, payload: str = "") -> str:
    if cmd in (TcpCommand.UPDATE_TXT, TcpCommand.UPDATE_KEY) and payload:
        encoded = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
        return f"{cmd.value}|{encoded}"
    elif cmd in (TcpCommand.EXT_SET_GROUP, TcpCommand.DELETE_FILE) and payload:
        return f"{cmd.value}|{payload}"
    else:
        return f"{cmd.value}|"
