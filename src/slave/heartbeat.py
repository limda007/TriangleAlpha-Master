"""UDP 心跳广播"""
from __future__ import annotations

import asyncio
import os
import platform
import socket
from collections.abc import Callable
from pathlib import Path

import psutil

from common.protocol import (
    HEARTBEAT_INTERVAL,
    UDP_PORT,
    build_udp_account_sync,
    build_udp_ext_online,
    build_udp_need_kami,
    build_udp_offline,
    build_udp_status,
)
from slave.logging_utils import get_logger


def _read_version() -> str:
    """从 pyproject.toml 读取版本号。"""
    import sys
    if getattr(sys, "frozen", False):
        toml_path = Path(getattr(sys, "_MEIPASS", "")) / "pyproject.toml"
    else:
        toml_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if toml_path.exists():
        for line in toml_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


SLAVE_VERSION = _read_version()
logger = get_logger(__name__)


class HeartbeatService:
    def __init__(
        self,
        master_ip: str | None = None,
        port: int = UDP_PORT,
        interval: int = HEARTBEAT_INTERVAL,
        on_sent: Callable[[int, float, float], None] | None = None,
        base_dir: Path | None = None,
    ):
        self._master_ip = master_ip
        self._port = port
        self._interval = interval
        self._machine_name = platform.node()
        self._user_name = os.getenv("USERNAME", os.getenv("USER", "unknown"))
        self._group = "默认"
        self._running = False
        self._on_sent = on_sent
        self._beat_count = 0
        self._base_dir = base_dir
        # P0: 预热 CPU 采样基准值，避免首次 interval=0 返回 0.0
        psutil.cpu_percent()

    @property
    def machine_name(self) -> str:
        return self._machine_name

    @property
    def group(self) -> str:
        return self._group

    def set_group(self, group: str) -> None:
        self._group = group

    def send_status(self, state: str, level: int = 0,
                    jin_bi: str = "0", desc: str = "",
                    elapsed: str = "0", status_text: str = "") -> None:
        """发送 STATUS 消息到 master（独立阻塞 UDP socket，不复用心跳 async socket）"""
        msg = build_udp_status(self._machine_name, state, level, jin_bi, desc, elapsed, status_text)
        self._send_udp(msg)

    def send_account_sync(self, payload_b64: str) -> None:
        """发送 ACCOUNT_SYNC 消息到 master。"""
        msg = build_udp_account_sync(self._machine_name, payload_b64)
        self._send_udp(msg)

    def send_need_kami(self) -> None:
        """发送 NEED_KAMI 请求到 master。"""
        msg = build_udp_need_kami(self._machine_name)
        self._send_udp(msg)
        logger.info("已发送 NEED_KAMI 请求")

    def check_kami_on_start(self) -> None:
        """启动时检查本地 kamis.txt，没有或为空则请求卡密。"""
        if not self._base_dir:
            return
        kami_file = self._base_dir / "kamis.txt"
        if not kami_file.exists() or not kami_file.read_text(encoding="utf-8-sig").strip():
            logger.info("本地 kamis.txt 不存在或为空，向 master 请求卡密")
            self.send_need_kami()

    def check_kami_periodic(self) -> None:
        """心跳周期检查 kamis.txt，没有或为空则请求卡密。"""
        if not self._base_dir:
            return
        kami_file = self._base_dir / "kamis.txt"
        if not kami_file.exists() or not kami_file.read_text(encoding="utf-8-sig").strip():
            self.send_need_kami()

    def _send_udp(self, msg: str) -> None:
        """通用 UDP 发送（独立阻塞 socket）。"""
        data = msg.encode("utf-8")
        target = (self._master_ip, self._port) if self._master_ip else ("255.255.255.255", self._port)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(data, target)

    async def run(self) -> None:
        self._running = True
        # 启动时检查卡密
        self.check_kami_on_start()
        # H2: 使用 with 管理 socket，CancelledError 时也能正确关闭
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
            # M1: 使用 get_running_loop() 替代已弃用的 get_event_loop()
            loop = asyncio.get_running_loop()
            consecutive_errors = 0

            try:
                while self._running:
                    try:
                        cpu = psutil.cpu_percent(interval=0)
                        mem = psutil.virtual_memory().percent
                        teammate_fill = self._read_teammate_fill()
                        weapon_config = self._read_config("武器配置.txt")
                        level_threshold = self._read_config("下号等级.txt")
                        loot_count = self._read_config("舔包次数.txt")
                        token_key = self._read_config("token.txt")
                        kami_code = self._read_kami_code()
                        msg = build_udp_ext_online(
                            self._machine_name,
                            self._user_name,
                            cpu,
                            mem,
                            SLAVE_VERSION,
                            self._group,
                            teammate_fill,
                            weapon_config,
                            level_threshold,
                            loot_count,
                            token_key,
                            kami_code,
                        )
                        data = msg.encode("utf-8")
                        target = (self._master_ip, self._port) if self._master_ip else ("255.255.255.255", self._port)
                        await loop.sock_sendto(sock, data, target)
                        self._beat_count += 1
                        if self._on_sent:
                            self._on_sent(self._beat_count, cpu, mem)
                        consecutive_errors = 0
                        # 每次心跳检查卡密
                        self.check_kami_periodic()
                    except Exception as e:
                        consecutive_errors += 1
                        logger.warning("心跳发送失败 (连续第 %s 次): %s", consecutive_errors, e)
                        if consecutive_errors >= 10:
                            logger.warning("心跳连续失败过多，等待 30 秒后重试")
                            await asyncio.sleep(30)
                            consecutive_errors = 0
                    await asyncio.sleep(self._interval)
            finally:
                # 发送离线通知（尽力而为）
                try:
                    sock.setblocking(True)
                    offline = build_udp_offline(self._machine_name).encode("utf-8")
                    target = (self._master_ip, self._port) if self._master_ip else ("255.255.255.255", self._port)
                    sock.sendto(offline, target)
                except Exception:
                    pass

    def stop(self) -> None:
        self._running = False

    # 配置文件默认值：文件不存在或为空时自动创建
    _CONFIG_DEFAULTS = {
        "补齐队友配置.txt": "0",
        "武器配置.txt": "G17_不带药",
        "下号等级.txt": "18",
        "舔包次数.txt": "8",
    }

    def _read_teammate_fill(self) -> str:
        """读取补齐队友配置，缺省时按关闭处理。"""
        return self._read_config("补齐队友配置.txt")

    def _read_kami_code(self) -> str:
        """读取本地卡密文件的首个非空值，用于心跳上报。"""
        if not self._base_dir:
            return ""
        kami_file = self._base_dir / "kamis.txt"
        try:
            if not kami_file.exists():
                return ""
            for line in kami_file.read_text(encoding="utf-8-sig").splitlines():
                value = line.strip()
                if value:
                    return value
        except OSError:
            return ""
        return ""

    def _read_config(self, filename: str) -> str:
        """读取指定配置文件内容，不存在或为空时自动创建默认值。"""
        default = self._CONFIG_DEFAULTS.get(filename, "")
        if not self._base_dir:
            return default
        cfg = self._base_dir / filename
        try:
            if cfg.exists():
                val = cfg.read_text(encoding="utf-8-sig").strip()
                if val:
                    return val
            # 文件不存在或为空 → 写入默认值
            cfg.write_text(default, encoding="utf-8")
            return default
        except OSError:
            return default
