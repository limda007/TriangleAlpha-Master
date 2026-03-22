"""TCP 指令接收与处理。"""
from __future__ import annotations

import asyncio
import base64
import binascii
import os
from collections.abc import Callable
from pathlib import Path

from common.protocol import TCP_CMD_PORT, ParsedTcpCommand, TcpCommand, parse_tcp_command
from slave.logging_utils import get_logger
from slave.process_manager import ProcessManager

logger = get_logger(__name__)


class CommandHandler:
    # H1: readline 缓冲区限制（1MB，支持大量账号的 base64 payload）
    STREAM_LIMIT = 1024 * 1024

    # 允许远程写入的配置文件白名单
    _CONFIG_WHITELIST = {"补齐队友配置.txt", "武器配置.txt", "下号等级.txt", "舔包次数.txt", "token.txt"}

    def __init__(
        self,
        base_dir: str,
        port: int = TCP_CMD_PORT,
        on_command: Callable[[str], None] | None = None,
        on_account_updated: Callable[[int], None] | None = None,
        on_group_changed: Callable[[str], None] | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._port = port
        self._pm = ProcessManager(base_dir)
        self._server: asyncio.AbstractServer | None = None
        self._group_callback: Callable[[str], None] | None = None
        self._on_command = on_command
        self._on_account_updated = on_account_updated
        self._on_group_changed = on_group_changed

    def set_group_callback(self, cb: Callable[[str], None]) -> None:
        self._group_callback = cb

    def _safe_path(self, filename: str) -> Path | None:
        """校验文件名安全性，拒绝路径遍历。"""
        fpath = (self._base_dir / filename).resolve()
        if not fpath.is_relative_to(self._base_dir.resolve()):
            logger.warning("拒绝路径遍历: %s", filename)
            return None
        return fpath

    async def run(self) -> None:
        max_retries = 5
        for attempt in range(max_retries):
            try:
                server = await asyncio.start_server(
                    self._handle_client,
                    "0.0.0.0",
                    self._port,
                    reuse_address=True,
                    limit=self.STREAM_LIMIT,
                    backlog=256,
                )
                self._server = server
                break
            except OSError as err:
                if attempt < max_retries - 1:
                    wait = 3 * (attempt + 1)
                    logger.warning("TCP 端口 %s 绑定失败 (第%s次): %s，%ss 后重试", self._port, attempt + 1, err, wait)
                    await asyncio.sleep(wait)
                else:
                    logger.error("TCP 端口 %s 绑定失败，已重试 %s 次，放弃", self._port, max_retries)
                    return
        logger.info("TCP 指令监听已启动，端口 %s", self._port)
        try:
            async with server:
                await server.serve_forever()
        finally:
            self._server = None

    async def stop(self) -> None:
        if self._server is None:
            return
        server = self._server
        self._server = None
        server.close()
        await server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername", ("unknown", 0))
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                await self._dispatch(text)
        except TimeoutError:
            pass
        except Exception:
            logger.exception("TCP 连接处理异常: %s", peer)
        finally:
            writer.close()
            await writer.wait_closed()

    async def _dispatch(self, text: str) -> None:
        desc = ""
        parsed = parse_tcp_command(text)
        if parsed is None:
            logger.warning("未知指令: %s", text[:50])
            return

        match parsed.command:
            case TcpCommand.UPDATE_TXT:
                desc = await self._handle_update_txt(parsed)
            case TcpCommand.UPDATE_KEY:
                desc = self._handle_update_key(parsed)
            case TcpCommand.START_EXE:
                desc = "启动脚本"
                logger.info("指令: %s", desc)
                await self._pm.start_launcher()
            case TcpCommand.STOP_EXE:
                desc = "停止脚本"
                logger.info("指令: %s", desc)
                await self._pm.stop_all()
            case TcpCommand.REBOOT_PC:
                desc = "重启电脑"
                logger.info("指令: %s", desc)
                if os.name == "nt":
                    await asyncio.create_subprocess_exec("shutdown", "-r", "-t", "0")
                else:
                    logger.warning("跳过重启：当前不是 Windows 系统")
            case TcpCommand.DELETE_FILE:
                desc = self._handle_delete_file(parsed)
            case TcpCommand.EXT_SET_GROUP:
                desc = self._handle_set_group(parsed)
            case TcpCommand.EXT_SET_CONFIG:
                desc = self._handle_set_config(parsed)

        if desc and self._on_command:
            self._on_command(desc)

    async def _handle_update_txt(self, parsed: ParsedTcpCommand) -> str:
        """处理账号更新指令。"""
        try:
            content = base64.b64decode(parsed.payload).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as err:
            logger.error("UPDATETXT 解码失败: %s", err)
            return ""
        (self._base_dir / "accounts.txt").write_text(content, encoding="utf-8")
        count = sum(1 for line in content.splitlines() if line.strip())
        desc = f"账号已更新 ({count}个)"
        logger.info("接收: %s", desc)
        if self._on_account_updated:
            self._on_account_updated(count)
        return desc

    def _handle_update_key(self, parsed: ParsedTcpCommand) -> str:
        """处理 Key 更新指令。"""
        try:
            key = base64.b64decode(parsed.payload).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as err:
            logger.error("UPDATEKEY 解码失败: %s", err)
            return ""
        (self._base_dir / "key.txt").write_text(key, encoding="utf-8")
        desc = "Key 已更新"
        logger.info("接收: %s", desc)
        return desc

    def _handle_delete_file(self, parsed: ParsedTcpCommand) -> str:
        """处理文件删除指令。"""
        deleted = 0
        for fname in parsed.payload.split("|"):
            fname_stripped = fname.strip()
            if not fname_stripped:
                continue
            fpath = self._safe_path(fname_stripped)
            if fpath is None:
                continue
            if fpath.exists():
                fpath.unlink()
                logger.info("删除: %s", fname_stripped)
                deleted += 1
            else:
                logger.info("忽略不存在的文件: %s", fname_stripped)
        return f"删除文件 ({deleted})"

    def _handle_set_group(self, parsed: ParsedTcpCommand) -> str:
        """处理分组设置指令。"""
        group = parsed.payload.strip()
        desc = f"设组: {group}"
        logger.info("分组已更新为: %s", group)
        if self._group_callback is not None:
            self._group_callback(group)
        if self._on_group_changed:
            self._on_group_changed(group)
        return desc

    def _handle_set_config(self, parsed: ParsedTcpCommand) -> str:
        """处理配置写入指令，支持 BASE64 编码的二进制内容。"""
        filename, sep, content = parsed.payload.partition("|")
        if not sep:
            logger.error("配置格式错误，需要 filename|content")
            return ""
        filename = filename.strip()
        fpath = self._safe_path(filename)
        if fpath is None:
            return ""
        # 支持 BASE64 前缀编码（二进制文件分发）
        if content.startswith("BASE64:"):
            try:
                raw = base64.b64decode(content[7:])
                fpath.write_bytes(raw)
            except Exception:
                logger.exception("BASE64 解码失败: %s", filename)
                return ""
        else:
            content = content.strip()
            fpath.write_text(content, encoding="utf-8")
        desc = f"文件已更新: {filename}"
        logger.info("配置: %s", desc)
        return desc
