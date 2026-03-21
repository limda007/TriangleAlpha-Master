"""Mock demo — 启动大屏界面，注入假节点 + 假账号，验证交互优化"""
import random
import sys
from pathlib import Path

# 确保 src 在 PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent / "src"))

from datetime import datetime, timedelta

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from master.app.core.account_db import AccountDB
from master.app.core.node_manager import NodeManager
from master.app.core.tcp_commander import TcpCommander
from master.app.view.account_interface import AccountInterface
from master.app.view.bigscreen_interface import BigScreenInterface

# ── 假数据 ──────────────────────────────────────────────

_MOCK_NODES = [
    ("PC-WORK-001", "192.168.1.101", "在线", "player_alpha", 32, "15800", "120"),
    ("PC-WORK-002", "192.168.1.102", "在线", "player_beta", 28, "9200", "85"),
    ("PC-WORK-003", "192.168.1.103", "在线", "player_gamma", 45, "32100", "240"),
    ("PC-WORK-004", "192.168.1.104", "在线", "", 0, "0", "0"),
    ("PC-WORK-005", "192.168.1.105", "运行中", "player_delta", 18, "4500", "35"),
    ("PC-WORK-006", "192.168.1.106", "运行中", "player_epsilon", 22, "7800", "55"),
    ("PC-WORK-007", "192.168.1.107", "离线", "", 0, "0", "0"),
    ("PC-WORK-008", "192.168.1.108", "断连", "", 15, "3200", "0"),
    ("PC-WORK-009", "192.168.1.109", "在线", "player_zeta", 50, "88000", "480"),
    ("PC-WORK-010", "192.168.1.110", "在线", "player_eta", 35, "21000", "180"),
]

_MOCK_ACCOUNTS = """\
player_alpha----Pass123!----alpha@mail.com----mailpass1----备注A
player_beta----Pass456!----beta@mail.com----mailpass2----备注B
player_gamma----Pass789!----gamma@mail.com----mailpass3----
player_delta----PassABC!----delta@mail.com----mailpass4----备注D
player_epsilon----PassDEF!----epsilon@mail.com----mailpass5----
player_zeta----PassGHI!----zeta@mail.com----mailpass6----备注F
player_eta----PassJKL!----eta@mail.com----mailpass7----
free_account_1----FreePass1!----free1@mail.com----freemail1----空闲
free_account_2----FreePass2!----free2@mail.com----freemail2----空闲
free_account_3----FreePass3!----free3@mail.com----freemail3----空闲
done_account_1----DonePass1!----done1@mail.com----donemail1----已完成测试
done_account_2----DonePass2!----done2@mail.com----donemail2----已完成测试
done_account_3----DonePass3!----done3@mail.com----donemail3----已完成测试
"""


def _inject_mock_nodes(nm: NodeManager) -> None:
    """往 NodeManager 注入假节点"""
    from common.models import NodeInfo

    for name, ip, status, account, level, jinbi, elapsed in _MOCK_NODES:
        node = NodeInfo(
            machine_name=name,
            ip=ip,
            status=status,
            current_account=account,
            level=level,
            jin_bi=jinbi,
            elapsed=elapsed,
            last_seen=datetime.now() - timedelta(seconds=random.randint(0, 60)),
            last_status_update=datetime.now() - timedelta(seconds=random.randint(0, 120)),
        )
        is_new = name not in nm.nodes
        nm.nodes[name] = node
        if is_new:
            nm.node_online.emit(name)
        nm.node_updated.emit(name)
    nm._recalc_online()
    nm.stats_changed.emit()


def _inject_mock_accounts(pool: AccountDB) -> None:
    """导入假账号并模拟部分绑定 + 已完成"""
    pool.import_fresh(_MOCK_ACCOUNTS)
    # 模拟已分配（运行中）
    for name, _, status, account, *_ in _MOCK_NODES:
        if account and status not in ("离线", "断连"):
            pool.allocate(name)
    # 模拟 3 个已完成账号
    for username in ("done_account_1", "done_account_2", "done_account_3"):
        pool._conn.execute(
            "UPDATE accounts SET status='运行中', assigned_machine='MOCK-DONE' "
            "WHERE username=?", (username,),
        )
    pool._conn.commit()
    for _ in range(3):
        pool.complete("MOCK-DONE", level=random.randint(30, 50))
    pool._refresh_counts()
    pool.pool_changed.emit()


def _patch_tcp(tcp: TcpCommander) -> None:
    """让 TCP 发送变成纯打印，不真实连接"""
    def fake_send(ip, cmd, payload=""):
        print(f"[MOCK TCP] send → {ip}  cmd={cmd}  payload={payload[:60]!r}")

    def fake_broadcast(ips, cmd, payload=""):
        for ip in ips:
            fake_send(ip, cmd, payload)

    tcp.send = fake_send
    tcp.broadcast = fake_broadcast


def main():
    app = QApplication(sys.argv)

    # 用内存临时数据库
    import tempfile
    db_path = Path(tempfile.mkdtemp()) / "mock_accounts.db"

    nm = NodeManager()
    tcp = TcpCommander()
    pool = AccountDB(db_path)

    _patch_tcp(tcp)

    # ── 创建大屏界面（独立窗口） ──
    bigscreen = BigScreenInterface(nm, tcp, pool)
    bigscreen.setWindowTitle("大屏模式 — Mock Demo")
    bigscreen.resize(1200, 800)

    # ── 创建账号管理界面（独立窗口） ──
    account_ui = AccountInterface(pool)
    account_ui.setWindowTitle("账号管理 — Mock Demo")
    account_ui.resize(900, 600)

    # 注入假数据（延迟 200ms，让 UI 先渲染）
    QTimer.singleShot(200, lambda: _inject_mock_nodes(nm))
    QTimer.singleShot(300, lambda: _inject_mock_accounts(pool))

    bigscreen.show()
    account_ui.show()

    print("=" * 60)
    print("Mock Demo 已启动！")
    print("  - 大屏界面: 10 个假节点 (8 在线, 1 离线, 1 断连)")
    print("  - 账号管理: 10 个假账号 (含已绑定 + 空闲)")
    print()
    print("验证要点:")
    print("  1. Shift/Ctrl 多选节点 → 按钮文案变 '(N台)'")
    print("  2. 选中后点按钮 → 仅操作选中在线节点")
    print("  3. 右键节点 → 弹出上下文菜单")
    print("  4. 重启/停止/删除 → 二次确认对话框")
    print("  5. 选中状态栏显示/隐藏")
    print("  6. 账号表格支持多选 + 右键释放绑定")
    print("=" * 60)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
