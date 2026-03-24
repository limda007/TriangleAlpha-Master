"""平台同步编排器 — QObject + QThread + QTimer"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from common.models import PLATFORM_ACCOUNT_HEADER, AccountInfo
from master.app.core.platform_client import PlatformAPIError, PlatformClient

if TYPE_CHECKING:
    from master.app.core.account_db import AccountDB

logger = logging.getLogger(__name__)

_MAX_AUTH_FAILURES = 3
_UPLOAD_BATCH_SIZE = 200


class _SyncWorker(QThread):
    """后台线程：执行上传、轮询、连接测试或 Token 刷新"""

    upload_done = pyqtSignal(list)       # 上传成功的 username 列表
    poll_done = pyqtSignal(list)         # 平台已取号的 username 列表
    tokens_updated = pyqtSignal(str, str)  # (access_token, refresh_token)
    error_occurred = pyqtSignal(str)

    def __init__(
        self, client_cfg: PlatformClient, task: str,
        upload_text: str = "", group_name: str = "",
        upload_usernames: list[str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._client_cfg = client_cfg
        self._task = task  # "upload" / "poll" / "connect" / "refresh"
        self._upload_text = upload_text
        self._group_name = group_name
        self._upload_usernames = upload_usernames or []

    def run(self) -> None:
        try:
            with httpx.Client() as http:
                if self._task == "upload":
                    self._do_upload(http)
                elif self._task == "poll":
                    self._do_poll(http)
                elif self._task == "connect":
                    self._do_connect(http)
                elif self._task == "refresh":
                    self._do_refresh(http)
        except PlatformAPIError as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(f"平台同步异常：{e}")

    def _do_connect(self, http: httpx.Client) -> None:
        """登录验证连接（始终发起网络请求验证凭据有效性）"""
        if self._client_cfg.refresh_token:
            try:
                self._client_cfg.refresh(http)
            except PlatformAPIError:
                self._client_cfg.login(http)
        else:
            self._client_cfg.login(http)
        self.tokens_updated.emit(
            self._client_cfg.access_token,
            self._client_cfg.refresh_token,
        )

    def _do_refresh(self, http: httpx.Client) -> None:
        """主动刷新 Token"""
        if self._client_cfg.refresh_token:
            self._client_cfg.refresh(http)
        else:
            self._client_cfg.login(http)
        self.tokens_updated.emit(
            self._client_cfg.access_token,
            self._client_cfg.refresh_token,
        )

    def _do_upload(self, http: httpx.Client) -> None:
        result = self._client_cfg.import_accounts(
            http, self._upload_text, self._group_name,
        )
        # 上传成功后发射 token 更新信号（可能经过了 refresh/re-login）
        self.tokens_updated.emit(
            self._client_cfg.access_token,
            self._client_cfg.refresh_token,
        )
        self.upload_done.emit(self._upload_usernames)
        count = result.get("imported", len(self._upload_usernames))
        logger.info("平台上传成功：%d 个账号", count)

    def _do_poll(self, http: httpx.Client) -> None:
        items = self._client_cfg.query_accounts(http, self._group_name)
        self.tokens_updated.emit(
            self._client_cfg.access_token,
            self._client_cfg.refresh_token,
        )
        taken = [item.get("steam_account", "") for item in items if item.get("steam_account")]
        if taken:
            self.poll_done.emit(taken)
            logger.info("平台轮询：%d 个账号已被取号", len(taken))


class PlatformSyncer(QObject):
    """平台同步编排器

    职责：
    - 监听 AccountDB.pool_changed → 节流后上传已完成账号
    - 定时轮询平台 → 标记已取号账号
    - 启动时扫描已有未上传账号
    - 上传失败 120s 后自动重试
    - 连续认证失败自动暂停
    """

    error_occurred = pyqtSignal(str)
    upload_finished = pyqtSignal(int)  # 上传成功数量
    # 状态变化: "已连接" / "未连接" / "连接失败"
    status_changed = pyqtSignal(str)

    def __init__(self, account_db: AccountDB, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db = account_db
        self._enabled = False
        self._api_url = ""
        self._username = ""
        self._password = ""
        self._group_name = ""
        self._client = PlatformClient()
        self._worker: _SyncWorker | None = None
        self._consecutive_auth_failures = 0

        # 同步统计
        self._total_uploaded = 0
        self._total_taken = 0
        self._last_sync_time = ""

        # 节流上传检查（Architect #4）
        self._upload_dirty = False
        self._resume_upload_after_worker = False
        self._upload_throttle = QTimer(self)
        self._upload_throttle.setSingleShot(True)
        self._upload_throttle.setInterval(5000)
        self._upload_throttle.timeout.connect(self._flush_upload)

        # 定时轮询（60s）
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(60_000)
        self._poll_timer.timeout.connect(self._do_poll)

        # 失败重试定时器（120s）（Critic #8）
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(120_000)
        self._retry_timer.timeout.connect(self.try_upload_completed)

        # Token 主动刷新（30 分钟）
        self._token_timer = QTimer(self)
        self._token_timer.setInterval(30 * 60_000)
        self._token_timer.timeout.connect(self._do_refresh)

    def configure(
        self, *, enabled: bool, api_url: str,
        username: str, password: str, group_name: str,
    ) -> None:
        """配置平台参数"""
        self._enabled = enabled
        self._api_url = api_url.rstrip("/") if api_url else ""
        self._username = username
        self._password = password
        self._group_name = group_name or username

        self._client = PlatformClient(
            base_url=self._api_url,
            username=self._username,
            password=self._password,
        )

        # 恢复已保存的 token
        at = self._db.get_config("platform_access_token")
        rt = self._db.get_config("platform_refresh_token")
        if at:
            self._client.access_token = at
        if rt:
            self._client.refresh_token = rt

        self._consecutive_auth_failures = 0

        # 始终重启（支持改密码后立即重连）
        self._stop_timers()
        if enabled and self._api_url:
            self.status_changed.emit("未连接")
            self.start()
        else:
            self.status_changed.emit("未连接")

    def start(self) -> None:
        """启动：立即连接测试 + 定时轮询 + 定时刷新 Token"""
        if not self._enabled or not self._api_url:
            return
        self._poll_timer.start()
        self._retry_timer.start()
        self._token_timer.start()
        # 立即连接测试
        QTimer.singleShot(500, self._do_connect)
        # 5s 后扫描已有未上传账号（Critic #6）
        QTimer.singleShot(5000, self.try_upload_completed)

    def stop(self) -> None:
        """停止所有定时器和工作线程"""
        self._stop_timers()
        w = self._worker
        if w is not None:
            try:
                if w.isRunning():
                    w.quit()
                    w.wait(3000)
            except RuntimeError:
                pass
            self._worker = None

    def _stop_timers(self) -> None:
        self._poll_timer.stop()
        self._retry_timer.stop()
        self._upload_throttle.stop()
        self._token_timer.stop()

    # ── pool_changed 节流上传 ──

    def on_pool_changed(self) -> None:
        """AccountDB.pool_changed 信号槽 — 节流 5s"""
        if not self._enabled:
            return
        self._upload_dirty = True
        if not self._upload_throttle.isActive():
            self._upload_throttle.start()

    def _flush_upload(self) -> None:
        if self._upload_dirty:
            self._upload_dirty = False
            self.try_upload_completed()

    # ── 上传逻辑 ──

    def try_upload_completed(self) -> None:
        """查询未上传的已完成账号并上传到平台"""
        if not self._enabled or not self._api_url:
            return
        # 互斥：worker 正在运行时跳过（Architect #3）
        if self._worker and self._worker.isRunning():
            self._upload_dirty = True  # 标记脏位，worker 完成后重新检查
            self._resume_upload_after_worker = True
            return
        pending = self._db.get_completed_not_uploaded(limit=_UPLOAD_BATCH_SIZE)
        if not pending:
            return
        text = self._build_upload_text(pending)
        usernames = [account.username for account in pending]

        self._worker = _SyncWorker(
            self._client, "upload",
            upload_text=text,
            group_name=self._group_name,
            upload_usernames=usernames,
            parent=self,
        )
        self._worker.upload_done.connect(self._on_upload_done)
        self._worker.tokens_updated.connect(self._on_tokens_updated)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.start()

    def _on_upload_done(self, usernames: list[str]) -> None:
        """上传成功 → 主线程标记 DB（Architect #5）"""
        count = self._db.mark_uploaded(usernames)
        self._total_uploaded += count
        self._last_sync_time = datetime.now().strftime("%H:%M:%S")
        self.upload_finished.emit(count)
        self.status_changed.emit("已连接")
        # 满批次上传后继续拉下一批，避免 >200 账号时一次性卡住主线程/平台。
        if len(usernames) >= _UPLOAD_BATCH_SIZE:
            self._upload_dirty = True
        if self._upload_dirty:
            self._resume_upload_after_worker = True

    @staticmethod
    def _build_upload_text(accounts: list[AccountInfo]) -> str:
        """构造销售平台上传文本：表头 + 数据行。"""
        lines = [PLATFORM_ACCOUNT_HEADER]
        lines.extend(account.to_platform_line() for account in accounts)
        return "\n".join(lines)

    # ── 连接测试 / Token 刷新 ──

    def _do_connect(self) -> None:
        """启动时连接测试"""
        if not self._enabled or not self._api_url:
            return
        if self._worker and self._worker.isRunning():
            return
        self._worker = _SyncWorker(self._client, "connect", parent=self)
        self._worker.tokens_updated.connect(self._on_tokens_updated)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.start()

    def _do_refresh(self) -> None:
        """定时刷新 Token（30 分钟心跳）"""
        if not self._enabled or not self._api_url:
            return
        if self._worker and self._worker.isRunning():
            return
        self._worker = _SyncWorker(self._client, "refresh", parent=self)
        self._worker.tokens_updated.connect(self._on_tokens_updated)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.start()

    # ── 轮询逻辑 ──

    def _do_poll(self) -> None:
        """定时轮询平台取号状态"""
        if not self._enabled or not self._api_url:
            return
        if self._worker and self._worker.isRunning():
            return
        self._worker = _SyncWorker(
            self._client, "poll",
            group_name=self._group_name,
            parent=self,
        )
        self._worker.poll_done.connect(self._on_poll_done)
        self._worker.tokens_updated.connect(self._on_tokens_updated)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.start()

    def _on_poll_done(self, usernames: list[str]) -> None:
        """轮询成功 → 主线程更新 DB（Architect #5）"""
        self._db.mark_taken_by_platform(usernames)
        self._total_taken += len(usernames)
        self._last_sync_time = datetime.now().strftime("%H:%M:%S")
        self.status_changed.emit("已连接")

    # ── Token / 错误处理 ──

    def _cleanup_worker(self) -> None:
        """Worker 完成后清理引用，防止访问已销毁的 C++ 对象"""
        w = self._worker
        self._worker = None
        if w is not None:
            w.deleteLater()
        if self._resume_upload_after_worker:
            self._resume_upload_after_worker = False
            QTimer.singleShot(0, self._resume_pending_upload)

    def _resume_pending_upload(self) -> None:
        """在任意后台任务结束后恢复待处理上传。"""
        if not self._upload_dirty:
            return
        self._upload_dirty = False
        self.try_upload_completed()

    def _on_tokens_updated(self, at: str, rt: str) -> None:
        """Token 更新 → 持久化到 DB（主线程）"""
        self._consecutive_auth_failures = 0
        self._client.access_token = at
        self._client.refresh_token = rt
        self._last_sync_time = datetime.now().strftime("%H:%M:%S")
        self.status_changed.emit("已连接")
        if at:
            self._db.set_config("platform_access_token", at)
        if rt:
            self._db.set_config("platform_refresh_token", rt)

    def _on_worker_error(self, msg: str) -> None:
        """Worker 错误处理 + 连续认证失败保护（Critic #7）"""
        logger.warning("平台同步错误：%s", msg)
        self.status_changed.emit("连接失败")
        if "登录失败" in msg or "刷新令牌失败" in msg:
            self._consecutive_auth_failures += 1
            if self._consecutive_auth_failures >= _MAX_AUTH_FAILURES:
                self._stop_timers()
                self.error_occurred.emit("连续认证失败，已暂停平台同步。请检查设置。")
                return
        self.error_occurred.emit(msg)

    # ── 统计属性 ──

    @property
    def total_uploaded(self) -> int:
        return self._total_uploaded

    @property
    def total_taken(self) -> int:
        return self._total_taken

    @property
    def last_sync_time(self) -> str:
        return self._last_sync_time
