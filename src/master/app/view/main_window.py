"""主窗口"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, InfoBar, InfoBarPosition, NavigationItemPosition

from common.models import NodeInfo
from common.protocol import ACCOUNT_RUNTIME_CLEANUP_PAYLOAD, GameState, TcpCommand
from master.app.common.config import RESOURCE_DIR, cfg
from master.app.core.account_db import AccountDB
from master.app.core.kami_db import KamiDB
from master.app.core.log_receiver import LogReceiverThread
from master.app.core.node_manager import NodeManager
from master.app.core.platform_syncer import PlatformSyncer
from master.app.core.tcp_commander import TcpCommander
from master.app.core.udp_listener import UdpListenerThread
from master.app.view.account_interface import AccountInterface
from master.app.view.bigscreen_interface import BigScreenInterface
from master.app.view.help_interface import HelpInterface
from master.app.view.history_interface import HistoryInterface
from master.app.view.kami_interface import KamiInterface
from master.app.view.log_interface import LogInterface
from master.app.view.setting_interface import SettingInterface

_ACCOUNT_REQUEST_STATUS_HINTS = ("向中控申请", "申请账号", "无可用账号")


def _get_db_path() -> Path:
    """accounts.db 放在 master exe 同目录"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "accounts.db"
    return Path(__file__).resolve().parents[4] / "accounts.db"


def _ensure_bound_account_cache(window: object) -> dict[str, str]:
    """兼容测试桩对象，确保绑定账号缓存始终可用。"""
    window_obj = cast(Any, window)
    cache = getattr(window_obj, "_bound_account_cache", None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    window_obj._bound_account_cache = cache
    return cache


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()

        # 核心服务
        self.nodeManager = NodeManager(self)
        self.tcpCommander = TcpCommander(parent=self)
        self.accountPool = AccountDB(_get_db_path(), parent=self)
        self.kamiDB = KamiDB(_get_db_path(), parent=self, conn=self.accountPool._conn)
        self.udpListener = UdpListenerThread(port=cfg.get(cfg.udpPort), parent=self)
        self.udpListener.message_received.connect(self.nodeManager.handle_udp_message)
        self.logReceiver = LogReceiverThread(port=cfg.get(cfg.tcpLogPort), parent=self)
        self.logReceiver.error_occurred.connect(self._onLogReceiverError)

        # 页面
        self.bigscreenInterface = BigScreenInterface(
            self.nodeManager, self.tcpCommander, self.accountPool, self,
            kami_db=self.kamiDB,
        )
        self.accountInterface = AccountInterface(self.accountPool, self)
        self.kamiInterface = KamiInterface(
            self.kamiDB, self.nodeManager, self.tcpCommander, self,
        )
        self.historyInterface = HistoryInterface(self.nodeManager, self)
        self.logInterface = LogInterface(self)
        self.logInterface.set_receiver(self.logReceiver)
        self.settingInterface = SettingInterface(self)
        self.helpInterface = HelpInterface(self)

        self._initWindow()
        self._initNavigation()

        # 启动
        self.udpListener.start()
        self.logReceiver.start()
        self._timeoutTimer = QTimer(self)
        self._timeoutTimer.timeout.connect(self.nodeManager.check_timeouts)
        self._timeoutTimer.start(5000)

        # 节点重连时自动重发绑定账号
        self.nodeManager.node_online.connect(self._onNodeReconnect)

        # 节点上报时自动补全缺失的验证码 Key
        self._tokenPushedAt: dict[str, float] = {}  # machine_name → monotonic timestamp
        self._TOKEN_PUSH_RETRY_SEC = 30  # 推送后 N 秒内 key 仍不匹配则允许重试
        self._completed_cleanup_sent: dict[str, str] = {}
        self.nodeManager.node_updated.connect(self._autoFixTokenKey)

        # slave STATUS 上报 → 同步等级/金币/状态到 AccountDB
        self.nodeManager.node_status_reported.connect(self._syncAccountFromNode)
        self._account_retry_sent_at: dict[str, float] = {}
        self._ACCOUNT_RETRY_SEC = 10
        # 缓存每个节点上一次校验过的绑定账号，避免每条 STATUS 都查 DB
        self._bound_account_cache: dict[str, str] = {}  # machine_name → username
        self.nodeManager.node_status_reported.connect(self._retry_missed_account_request)

        # slave ACCOUNT_SYNC → 批量 upsert 账号到 AccountDB
        self.nodeManager.account_sync_received.connect(self._onAccountSync)

        # TestDemo NEED_ACCOUNT → 自动分配空闲账号
        self.nodeManager.need_account.connect(self._onNeedAccount)

        # 定期清理离线节点和追踪字典（每 30 分钟）
        self._purgeTimer = QTimer(self)
        self._purgeTimer.timeout.connect(self._purgeStaleData)
        self._purgeTimer.start(30 * 60_000)

        # 平台同步
        self._initPlatformSyncer()

    def _initNavigation(self):
        self.addSubInterface(self.bigscreenInterface, FIF.COMMAND_PROMPT, "大屏模式")
        self.navigationInterface.addSeparator()
        self.addSubInterface(self.accountInterface, FIF.PEOPLE, "账号管理", NavigationItemPosition.SCROLL)
        self.addSubInterface(self.kamiInterface, FIF.LABEL, "卡密管理", NavigationItemPosition.SCROLL)
        self.addSubInterface(self.historyInterface, FIF.HISTORY, "操作历史", NavigationItemPosition.SCROLL)
        self.addSubInterface(self.logInterface, FIF.DOCUMENT, "实时日志", NavigationItemPosition.SCROLL)
        self.addSubInterface(self.helpInterface, FIF.HELP, "帮助", NavigationItemPosition.BOTTOM)
        self.addSubInterface(
            self.settingInterface, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM,
        )

    def _initWindow(self):
        self.resize(1600, 800)
        self.setMinimumWidth(900)
        self.setWindowTitle("TriangleAlpha 群控中心")

        # 应用图标
        icon_path = RESOURCE_DIR / "icon_256.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        if sys.platform != "darwin":
            self.navigationInterface.setAcrylicEnabled(True)
        self.navigationInterface.setExpandWidth(168)

        if sys.platform == "darwin":
            self.setMicaEffectEnabled(False)
            self._fixMacOSTitleBar()

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.width() // 2 - self.width() // 2, geo.height() // 2 - self.height() // 2)

    def closeEvent(self, e):
        self.platformSyncer.stop()      # 首行，确保 worker 停止
        self.kamiDB.close()
        self.accountPool.close()
        self.udpListener.stop()
        self.logReceiver.stop()
        self.tcpCommander.stop()
        super().closeEvent(e)

    def _fixMacOSTitleBar(self) -> None:
        self.setSystemTitleBarButtonVisible(True)
        self.titleBar.minBtn.hide()
        self.titleBar.maxBtn.hide()
        self.titleBar.closeBtn.hide()

    def _onLogReceiverError(self, msg: str) -> None:
        InfoBar.error(
            "日志服务异常", msg,
            parent=self, position=InfoBarPosition.TOP, duration=5000,
        )

    def _purgeStaleData(self) -> None:
        """定期清理：过期离线节点 + 追踪字典中已不存在的节点"""
        self.nodeManager.purge_stale_nodes()
        # 清理追踪字典中不再存在于 nodes 的条目
        active_nodes = set(self.nodeManager.nodes.keys())
        bound_account_cache = _ensure_bound_account_cache(self)
        for d in (
            self._tokenPushedAt,
            self._completed_cleanup_sent,
            self._account_retry_sent_at,
            bound_account_cache,
        ):
            stale_keys = [k for k in d if k not in active_nodes]
            for k in stale_keys:
                del d[k]

    def _initPlatformSyncer(self) -> None:
        self.platformSyncer = PlatformSyncer(self.accountPool, parent=self)
        self.platformSyncer.configure(
            enabled=cfg.get(cfg.platformEnabled),
            api_url=cfg.get(cfg.platformApiUrl),
            username=cfg.get(cfg.platformUsername),
            password=cfg.get(cfg.platformPassword),
            group_name=cfg.get(cfg.platformGroupName),
        )
        # pool_changed → 节流上传检查
        self.accountPool.pool_changed.connect(self.platformSyncer.on_pool_changed)
        # 设置页变更 → 重新配置 + 刷新大屏 tab
        self.settingInterface.platformSettingsChanged.connect(self._onPlatformSettingsChanged)
        # 错误/成功通知
        self.platformSyncer.error_occurred.connect(self._onPlatformError)
        self.platformSyncer.upload_finished.connect(self._onPlatformUploadDone)
        # 状态变化 → 大屏 tab
        self.platformSyncer.status_changed.connect(self._onPlatformStatusChanged)

    def _onPlatformSettingsChanged(self) -> None:
        self.platformSyncer.configure(
            enabled=cfg.get(cfg.platformEnabled),
            api_url=cfg.get(cfg.platformApiUrl),
            username=cfg.get(cfg.platformUsername),
            password=cfg.get(cfg.platformPassword),
            group_name=cfg.get(cfg.platformGroupName),
        )
        # 双向同步：设置页 → 大屏 tab
        self.bigscreenInterface.refreshPlatformFields()

    def _onPlatformError(self, msg: str) -> None:
        InfoBar.error(
            "平台同步异常", msg,
            parent=self, position=InfoBarPosition.TOP, duration=5000,
        )

    def _onPlatformUploadDone(self, count: int) -> None:
        if count > 0:
            InfoBar.success(
                "平台上传", f"成功上传 {count} 个账号",
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )
        self._syncPlatformStats()

    def _onPlatformStatusChanged(self, status: str) -> None:
        """PlatformSyncer 状态变化 → 更新大屏 tab"""
        self.bigscreenInterface.updatePlatformStatus(status)
        self._syncPlatformStats()

    def _syncPlatformStats(self) -> None:
        """同步平台统计到大屏 tab"""
        s = self.platformSyncer
        self.bigscreenInterface.updatePlatformStats(
            s.total_uploaded, s.total_taken, s.last_sync_time,
        )

    def _onNodeReconnect(self, machine_name: str) -> None:
        """节点上线时自动重发其绑定的账号"""
        acc = self.accountPool.get_account_for_machine(machine_name)
        if acc is None:
            return
        node = self.nodeManager.nodes.get(machine_name)
        if node:
            self._completed_cleanup_sent.pop(machine_name, None)
            self.tcpCommander.send(node.ip, TcpCommand.UPDATE_TXT, acc.to_line())

    def _autoFixTokenKey(self, machine_name: str) -> None:
        """节点缺少或 Key 不一致时自动下发正确的验证码 Key"""
        master_key = self.accountPool.get_config("api_key")
        if not master_key:
            return
        node = self.nodeManager.nodes.get(machine_name)
        if not node or node.status in ("离线", "断连"):
            return
        # Key 一致，清除推送记录（支持 master 换 key 后重新推送）
        if node.token_key == master_key:
            self._tokenPushedAt.pop(machine_name, None)
            return
        # 推送过但未超时 → 跳过；超时 → 允许重试
        pushed_at = self._tokenPushedAt.get(machine_name)
        if pushed_at is not None and time.monotonic() - pushed_at < self._TOKEN_PUSH_RETRY_SEC:
            return
        # 自动下发
        self.tcpCommander.send(node.ip, TcpCommand.EXT_SET_CONFIG, f"token.txt|{master_key}")
        self._tokenPushedAt[machine_name] = time.monotonic()

    def _syncAccountFromNode(self, machine_name: str) -> None:
        """slave STATUS 上报 → 同步等级/金币/状态到 AccountDB"""
        node = self.nodeManager.nodes.get(machine_name)
        if not node:
            return
        # 脚本停止时 game_state 被清空，跳过同步避免零值覆盖账号数据
        if not node.game_state:
            self._completed_cleanup_sent.pop(machine_name, None)
            return
        if node.game_state != GameState.COMPLETED:
            self._completed_cleanup_sent.pop(machine_name, None)
        # 双层防护：level 和 jin_bi 都是零值时跳过（IPC 超时/重启过渡期产生的无效数据）
        if node.level == 0 and (not node.jin_bi or node.jin_bi == "0"):
            return
        # 校验 current_account 与绑定账号一致，防止旧号数据写入新号
        # 使用缓存减少 DB 查询：仅在 current_account 变化时才查 DB
        bound_account_cache = _ensure_bound_account_cache(self)
        if node.current_account:
            cached_bound = bound_account_cache.get(machine_name)
            if cached_bound is None or cached_bound == "":
                # 缓存未命中，查 DB 并缓存
                bound = self.accountPool.get_account_for_machine(machine_name)
                cached_bound = bound.username if bound else ""
                bound_account_cache[machine_name] = cached_bound
            if cached_bound and cached_bound != node.current_account:
                return
        # 从 elapsed 反推登录时间，确保快速完成的账号也能记录
        login_at = ""
        if node.elapsed and node.elapsed != "0":
            try:
                elapsed_sec = int(node.elapsed)
                if elapsed_sec > 0:
                    login_at = (datetime.now() - timedelta(seconds=elapsed_sec)).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        self.accountPool.update_from_status(
            machine_name, node.level, node.jin_bi, node.game_state,
            current_account=node.current_account,
            login_at=login_at,
        )
        if node.game_state == GameState.COMPLETED:
            # 账号完成 → 清除绑定缓存，下次分配时重新查询
            bound_account_cache.pop(machine_name, None)
            self._request_slave_account_cleanup(machine_name)

    def _onAccountSync(self, machine_name: str, accounts: object) -> None:
        """slave ACCOUNT_SYNC → 同步账号数据 + 封禁检测"""
        if not isinstance(accounts, list):
            return
        node = self.nodeManager.nodes.get(machine_name)
        threshold = int(node.level_threshold) if node and node.level_threshold.isdigit() else 0
        self.accountPool.upsert_from_sync(machine_name, accounts, level_threshold=threshold)

    def _needs_account_remediation(self, node: NodeInfo) -> bool:
        """根据节点状态判断是否需要补发账号。"""
        if node.status in ("离线", "断连"):
            return False
        status_text = node.status_text.strip()
        if not status_text or not any(hint in status_text for hint in _ACCOUNT_REQUEST_STATUS_HINTS):
            return False
        bound = self.accountPool.get_account_for_machine(node.machine_name)
        if bound is None:
            return True
        current_account = node.current_account.strip()
        return not current_account or current_account != bound.username

    def _retry_missed_account_request(self, machine_name: str) -> None:
        """漏收 NEED_ACCOUNT 时，基于 STATUS 文案补发账号。"""
        node = self.nodeManager.nodes.get(machine_name)
        if node is None:
            return
        if not self._needs_account_remediation(node):
            self._account_retry_sent_at.pop(machine_name, None)
            return
        now = time.monotonic()
        last_retry = self._account_retry_sent_at.get(machine_name)
        if last_retry is not None and now - last_retry < self._ACCOUNT_RETRY_SEC:
            return
        self._account_retry_sent_at[machine_name] = now
        self._onNeedAccount(machine_name)

    def _onNeedAccount(self, machine_name: str) -> None:
        """TestDemo NEED_ACCOUNT → 自动分配账号并下发。

        优先重发该机器已绑定的未完成账号（accounts.json 被删等情况），
        无绑定账号时从池中分配新的空闲账号。
        """
        node = self.nodeManager.nodes.get(machine_name)
        if not node:
            return
        bound_account_cache = _ensure_bound_account_cache(self)
        self._completed_cleanup_sent.pop(machine_name, None)
        existing = self.accountPool.get_account_for_machine(machine_name)
        if existing:
            # 该机器有未完成账号 → 重新下发，让 TestDemo 继续跑
            bound_account_cache[machine_name] = existing.username
            self.tcpCommander.send(node.ip, TcpCommand.UPDATE_TXT, existing.to_line())
            return
        acc = self.accountPool.allocate(machine_name)
        if acc is None:
            return
        # 清空节点缓存，防止旧账号的等级/金币写入新账号
        node.level = 0
        node.jin_bi = "0"
        node.current_account = ""
        node.game_state = ""
        bound_account_cache[machine_name] = acc.username
        self.tcpCommander.send(node.ip, TcpCommand.UPDATE_TXT, acc.to_line())

    def _request_slave_account_cleanup(self, machine_name: str) -> None:
        """master 确认账号已完成后，通知 slave 清理本地残留账号文件。"""
        node = self.nodeManager.nodes.get(machine_name)
        if node is None or not node.ip:
            return
        cleanup_key = node.current_account.strip() or "__completed__"
        if self._completed_cleanup_sent.get(machine_name) == cleanup_key:
            return
        self.tcpCommander.send(
            node.ip,
            TcpCommand.DELETE_FILE,
            ACCOUNT_RUNTIME_CLEANUP_PAYLOAD,
        )
        self._completed_cleanup_sent[machine_name] = cleanup_key
