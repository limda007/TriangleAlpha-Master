"""TCP 指令接收与处理"""
from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import AsyncIterator
from pathlib import Path

from slave.process_manager import ProcessManager


class CommandHandler:
    def __init__(self, base_dir: str, port: int = 9999):
        self._base_dir = Path(base_dir)
        self._port = port
        self._pm = ProcessManager(base_dir)
        self._group_callback: object = None  # 设置分组回调

    def set_group_callback(self, cb: object) -> None:
        self._group_callback = cb

    async def run(self) -> None:
        server = await asyncio.start_server(self._handle_client, "0.0.0.0", self._port)
        print(f"[TCP] 指令监听已启动，端口 {self._port}")
        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not line:
                return
            text = line.decode("utf-8", errors="ignore").strip()
            if text:
                await self._dispatch(text, reader)
        except TimeoutError:
            pass
        except Exception as e:
            print(f"[TCP 异常] {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _dispatch(self, text: str, reader: asyncio.StreamReader) -> None:
        if text.startswith("UPDATETXT|"):
            payload = text[len("UPDATETXT|") :]
            content = base64.b64decode(payload).decode("utf-8")
            (self._base_dir / "accounts.txt").write_text(content, encoding="utf-8")
            print("[接收] 账号已更新")

        elif text.startswith("UPDATEKEY|"):
            payload = text[len("UPDATEKEY|") :]
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
            if os.name == "nt":
                os.system("shutdown -r -t 0")  # noqa: S605, S607
            else:
                print("[跳过] 非 Windows 系统，不执行重启")

        elif text.startswith("DELETEFILE|"):
            parts = text.split("|")[1:]
            for fname in parts:
                fname = fname.strip()
                if not fname:
                    continue
                fpath = self._base_dir / fname
                if fpath.exists():
                    fpath.unlink()
                    print(f"[删除] {fname}")
                else:
                    print(f"[忽略] 文件不存在: {fname}")

        elif text.startswith("EXT_SETGROUP|"):
            group = text[len("EXT_SETGROUP|") :]
            print(f"[分组] 设为: {group}")
            if self._group_callback and callable(self._group_callback):
                self._group_callback(group)

        elif text.startswith("SENDFILE_START|"):
            filename = text.split("|", 1)[1]
            print(f"[接收] 开始接收文件: {filename}")
            save_path = self._base_dir / filename
            with open(save_path, "wb") as f:
                async for chunk_line in self._read_chunks(reader):
                    if chunk_line.startswith("SENDFILE_CHUNK|"):
                        chunk_data = base64.b64decode(chunk_line[len("SENDFILE_CHUNK|") :])
                        f.write(chunk_data)
                    elif chunk_line.startswith("SENDFILE_END|"):
                        break
            print(f"[接收] 文件完成: {filename}")

        else:
            print(f"[未知指令] {text[:50]}")

    async def _read_chunks(self, reader: asyncio.StreamReader) -> AsyncIterator[str]:
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=60)
            if not line:
                break
            yield line.decode("utf-8", errors="ignore").strip()
