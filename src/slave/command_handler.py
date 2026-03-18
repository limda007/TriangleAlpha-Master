"""TCP 指令接收与处理"""
from __future__ import annotations

import asyncio
import base64
import binascii
import os
import tempfile
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from common.protocol import TCP_CMD_PORT
from slave.process_manager import ProcessManager


class CommandHandler:
    # 文件接收最大限制
    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
    # H1: readline 缓冲区限制（1MB，支持大量账号的 base64 payload）
    STREAM_LIMIT = 1024 * 1024

    def __init__(
        self,
        base_dir: str,
        # H4: 使用 protocol 常量替代硬编码
        port: int = TCP_CMD_PORT,
        on_command: Callable[[str], None] | None = None,
        on_account_updated: Callable[[int], None] | None = None,
        on_group_changed: Callable[[str], None] | None = None,
    ):
        self._base_dir = Path(base_dir)
        self._port = port
        self._pm = ProcessManager(base_dir)
        # H7: 使用正确的类型标注
        self._group_callback: Callable[[str], None] | None = None
        self._on_command = on_command
        self._on_account_updated = on_account_updated
        self._on_group_changed = on_group_changed

    def set_group_callback(self, cb: Callable[[str], None]) -> None:
        self._group_callback = cb

    def _safe_path(self, filename: str) -> Path | None:
        """校验文件名安全性，拒绝路径遍历"""
        fpath = (self._base_dir / filename).resolve()
        if not fpath.is_relative_to(self._base_dir.resolve()):
            print(f"[安全] 拒绝路径遍历: {filename}")
            return None
        return fpath

    async def run(self) -> None:
        max_retries = 5
        for attempt in range(max_retries):
            try:
                server = await asyncio.start_server(
                    self._handle_client, "0.0.0.0", self._port,
                    reuse_address=True,
                    # H1: 设置更大的缓冲区限制
                    limit=self.STREAM_LIMIT,
                )
                break
            except OSError as e:
                if attempt < max_retries - 1:
                    wait = 3 * (attempt + 1)
                    print(f"[TCP] 端口 {self._port} 绑定失败 (第{attempt + 1}次): {e}，{wait}s 后重试")
                    await asyncio.sleep(wait)
                else:
                    print(f"[TCP] 端口 {self._port} 绑定失败，已重试 {max_retries} 次，放弃")
                    return
        print(f"[TCP] 指令监听已启动，端口 {self._port}")
        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername", ("unknown", 0))
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                await self._dispatch(text, reader)
        except TimeoutError:
            pass
        except Exception as e:
            print(f"[TCP 异常] {peer}: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _dispatch(self, text: str, reader: asyncio.StreamReader) -> None:
        desc = ""

        if text.startswith("UPDATETXT|"):
            desc = await self._handle_update_txt(text)

        elif text.startswith("UPDATEKEY|"):
            desc = self._handle_update_key(text)

        elif text.startswith("STARTEXE|"):
            desc = "启动脚本"
            print(f"[指令] {desc}")
            await self._pm.start_testdemo()

        elif text.startswith("STOPEXE|"):
            desc = "停止脚本"
            print(f"[指令] {desc}")
            await self._pm.stop_all()

        elif text.startswith("REBOOTPC|"):
            desc = "重启电脑"
            print(f"[指令] {desc}")
            # C3/M8: 使用异步子进程替代 os.system，不阻塞事件循环
            if os.name == "nt":
                await asyncio.create_subprocess_exec("shutdown", "-r", "-t", "0")
            else:
                print("[跳过] 非 Windows 系统，不执行重启")

        elif text.startswith("DELETEFILE|"):
            desc = self._handle_delete_file(text)

        elif text.startswith("EXT_SETGROUP|"):
            desc = self._handle_set_group(text)

        elif text.startswith("SENDFILE_START|"):
            desc = await self._handle_sendfile(text, reader)

        else:
            print(f"[未知指令] {text[:50]}")

        if desc and self._on_command:
            self._on_command(desc)

    # ── 指令处理方法 ──────────────────────────────────

    async def _handle_update_txt(self, text: str) -> str:
        """处理账号更新指令"""
        payload = text[len("UPDATETXT|"):]
        # M3: base64 解码异常处理
        try:
            content = base64.b64decode(payload).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as e:
            print(f"[错误] UPDATETXT 解码失败: {e}")
            return ""
        (self._base_dir / "accounts.txt").write_text(content, encoding="utf-8")
        count = sum(1 for line in content.splitlines() if line.strip())
        desc = f"账号已更新 ({count}个)"
        print(f"[接收] {desc}")
        if self._on_account_updated:
            self._on_account_updated(count)
        return desc

    def _handle_update_key(self, text: str) -> str:
        """处理 Key 更新指令"""
        payload = text[len("UPDATEKEY|"):]
        try:
            key = base64.b64decode(payload).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as e:
            print(f"[错误] UPDATEKEY 解码失败: {e}")
            return ""
        (self._base_dir / "key.txt").write_text(key, encoding="utf-8")
        desc = "Key 已更新"
        print(f"[接收] {desc}")
        return desc

    def _handle_delete_file(self, text: str) -> str:
        """处理文件删除指令"""
        parts = text.split("|")[1:]
        deleted = 0
        for fname in parts:
            fname_stripped = fname.strip()
            if not fname_stripped:
                continue
            fpath = self._safe_path(fname_stripped)
            if fpath is None:
                continue
            if fpath.exists():
                fpath.unlink()
                print(f"[删除] {fname_stripped}")
                deleted += 1
            else:
                print(f"[忽略] 文件不存在: {fname_stripped}")
        return f"删除文件 ({deleted})"

    def _handle_set_group(self, text: str) -> str:
        """处理分组设置指令"""
        group = text[len("EXT_SETGROUP|"):]
        desc = f"设组: {group}"
        print(f"[分组] 设为: {group}")
        if self._group_callback is not None:
            self._group_callback(group)
        if self._on_group_changed:
            self._on_group_changed(group)
        return desc

    async def _handle_sendfile(self, text: str, reader: asyncio.StreamReader) -> str:
        """处理文件接收指令

        C4: 先写临时文件，完整接收并校验后原子 rename，防止 TOCTOU。
        """
        filename = text.split("|", 1)[1]
        save_path = self._safe_path(filename)
        if save_path is None:
            return ""

        print(f"[接收] 开始接收文件: {filename}")
        total_size = 0
        # C4: 写入临时文件，成功后原子替换
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self._base_dir), prefix=".recv_", suffix=".tmp",
        )
        try:
            with open(tmp_fd, "wb") as f:
                async for chunk_line in self._read_chunks(reader):
                    if chunk_line.startswith("SENDFILE_CHUNK|"):
                        try:
                            chunk_data = base64.b64decode(chunk_line[len("SENDFILE_CHUNK|"):])
                        except binascii.Error:
                            print("[错误] SENDFILE_CHUNK 解码失败")
                            break
                        total_size += len(chunk_data)
                        # C4: 先检查大小，再写入
                        if total_size > self.MAX_FILE_SIZE:
                            print(f"[拒绝] 文件超过大小限制: {total_size} > {self.MAX_FILE_SIZE}")
                            break
                        f.write(chunk_data)
                    elif chunk_line.startswith("SENDFILE_END|"):
                        break

            if total_size > self.MAX_FILE_SIZE:
                Path(tmp_path).unlink(missing_ok=True)
                return ""

            # C4: 原子替换目标文件
            Path(tmp_path).replace(save_path)
            print(f"[接收] 文件完成: {filename} ({total_size} bytes)")
            return f"文件: {filename}"

        except Exception as e:
            Path(tmp_path).unlink(missing_ok=True)
            print(f"[错误] 文件接收异常: {e}")
            return ""

    async def _read_chunks(self, reader: asyncio.StreamReader) -> AsyncIterator[str]:
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=60)
            if not line:
                break
            yield line.decode("utf-8", errors="replace").strip()
