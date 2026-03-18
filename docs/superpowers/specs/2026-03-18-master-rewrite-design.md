# TriangleAlpha Master — 群控中控重写设计

> 日期: 2026-03-18
> 状态: 已确认

## 1. 产品定位

50+ 节点规模的游戏群控中心，PyQt6 + Fluent Design 现代 UI。macOS 开发调试，Windows 部署运行。

## 2. 功能矩阵

| 模块 | 功能 | 来源 | 优先级 |
|------|------|------|--------|
| 节点管理 | 自动发现、在线/离线状态、分组/筛选/搜索 | 原版+新增 | P0 |
| 一键操作 | 启动/停止/重启脚本、重启电脑 | 原版 | P0 |
| 账号管理 | 账号池导入、自动分发、进度追踪、已完成记录 | 原版+优化 | P0 |
| 卡密管理 | 批量分发 key.txt | 原版 | P0 |
| 文件传输 | 发送文件/文件夹、远程删除 | 原版 | P0 |
| 设置 | 主题切换、端口配置、超时参数 | 新增 | P0 |
| 实时日志 | 远程查看被控端 Console 输出 | 新增 | P1 |
| 节点分组 | 按标签分组，批量操作选定分组 | 新增 | P1 |
| 操作历史 | 所有指令记录+时间戳+结果 | 新增 | P1 |
| 批量部署 | 一键推送 SlaveClient + TestDemo 到新节点 | 新增 | P2 |
| 统计仪表盘 | 在线率、账号消耗、等级分布图表 | 新增 | P2 |

## 3. 架构：单仓库 + 共享协议层

```
TriangleAlpha-Master/
├── pyproject.toml
├── src/
│   ├── common/                    # 共享层
│   │   ├── protocol.py            # 协议常量 + 消息解析/构建
│   │   ├── network.py             # UDP/TCP 工具函数
│   │   └── models.py              # 数据模型 (dataclass)
│   │
│   ├── master/                    # 中控端
│   │   ├── main.py                # 入口点
│   │   ├── app/
│   │   │   ├── common/
│   │   │   │   ├── config.py      # QConfig 配置
│   │   │   │   ├── signal_bus.py  # 信号总线
│   │   │   │   ├── style_sheet.py # QSS 主题
│   │   │   │   └── icon.py        # FluentIcon 扩展
│   │   │   ├── core/
│   │   │   │   ├── node_manager.py    # 节点状态管理
│   │   │   │   ├── udp_listener.py    # UDP 8888 监听线程
│   │   │   │   ├── tcp_commander.py   # TCP 9999 指令发送
│   │   │   │   ├── account_pool.py    # 账号池管理
│   │   │   │   └── file_transfer.py   # 文件传输逻辑
│   │   │   ├── view/
│   │   │   │   ├── main_window.py     # FluentWindow 主窗口
│   │   │   │   ├── dashboard_interface.py   # 仪表盘
│   │   │   │   ├── node_interface.py        # 节点管理
│   │   │   │   ├── account_interface.py     # 账号管理
│   │   │   │   ├── log_interface.py         # 实时日志
│   │   │   │   ├── history_interface.py     # 操作历史
│   │   │   │   └── setting_interface.py     # 设置
│   │   │   ├── components/
│   │   │   │   ├── stat_card.py       # 统计卡片
│   │   │   │   └── node_group.py      # 节点分组组件
│   │   │   └── resource/
│   │   │       └── qss/              # light/ + dark/
│   │   └── build.spec
│   │
│   └── slave/                     # 被控端
│       ├── main.py
│       ├── heartbeat.py           # UDP 心跳广播
│       ├── command_handler.py     # TCP 指令处理
│       ├── process_manager.py     # 进程管理
│       ├── file_receiver.py       # 文件接收/解压
│       ├── auto_setup.py          # 自启动/改名/远控查杀
│       ├── log_reporter.py        # 日志上报
│       └── build.spec
│
├── docs/superpowers/specs/
└── tests/
    ├── test_protocol.py
    ├── test_node_manager.py
    └── test_account_pool.py
```

## 4. 通信协议

### 4.1 UDP 8888（被控→中控）

| 消息 | 格式 |
|------|------|
| 心跳 | `ONLINE\|{机器名}\|{用户名}` |
| 离线 | `OFFLINE\|{机器名}` |
| 状态 | `STATUS\|{机器名}\|{State}\|{Level}\|{JinBi}\|{Desc}` |
| 扩展心跳 | `EXT_ONLINE\|{机器名}\|{用户名}\|{CPU%}\|{MEM%}\|{版本号}\|{分组}` |

### 4.2 TCP 9999（中控→被控）

原版指令全部保留：`UPDATETXT|`, `SENDFILE_START|`, `SENDFILE_CHUNK|`, `SENDFILE_END|`, `STARTEXE|`, `STOPEXE|`, `REBOOTPC|`, `UPDATEKEY|`, `SENDFOLDER_START|`, `SENDFOLDER_CHUNK|`, `SENDFOLDER_END|`, `DELETEFILE|`

新增：`EXT_QUERY|`, `EXT_SETGROUP|{标签}`

### 4.3 TCP 8890（被控→中控，新增）

| 消息 | 格式 |
|------|------|
| 日志 | `LOG\|{机器名}\|{时间戳}\|{级别}\|{内容}` |

### 4.4 超时规则

| 规则 | 阈值 | 行为 |
|------|------|------|
| 离线判定 | 15秒无心跳 | 标记"离线" |
| 断线判定 | 60秒无心跳 | 标记"断线"，沉底 |
| TCP 发送超时 | 10秒 | 标记"通信异常" |

## 5. 数据模型

```python
@dataclass
class NodeInfo:
    machine_name: str
    ip: str
    user_name: str = ""
    status: str = "在线"
    level: int = 0
    jin_bi: str = "0"
    current_account: str = ""
    group: str = "默认"
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    slave_version: str = ""
    last_seen: datetime = field(default_factory=datetime.now)
    last_status_update: datetime = field(default_factory=datetime.now)
```

## 6. UI 页面

### 6.1 侧边栏导航

仪表盘 / 节点管理 / 账号管理 / 操作历史 / 实时日志 / 设置(底部)

### 6.2 仪表盘

顶部 4 个 StatCard：在线节点、总节点、账号消耗、运行时长。下方：节点状态饼图 + 最近操作。

### 6.3 节点管理（核心页面）

- 工具栏：搜索框、分组下拉、操作按钮（启动/停止/重启/分发卡密/发送文件）
- 表格列：勾选、状态灯、机器名、IP、分组、等级、金币、当前账号、CPU%、内存%、版本、最后心跳
- 支持多选、右键菜单、列排序、离线沉底灰显

### 6.4 账号管理

导入 txt/csv、账号池统计、表格（账号/密码掩码/状态/分配机器/等级/完成时间）、导出已完成。

### 6.5 操作历史

表格：时间、操作类型、目标节点、详情、结果。支持类型筛选和时间范围查询。

### 6.6 实时日志

左侧节点列表（可多选）、右侧日志流（自动滚动/可暂停）、关键词过滤、级别筛选。

### 6.7 设置

网络（端口/心跳间隔/超时）、外观（主题/主题色）、路径、关于。

## 7. 技术实现

### 7.1 依赖

```toml
dependencies = [
    "PyQt6>=6.7",
    "PyQt-Fluent-Widgets>=1.7.0",
    "psutil>=5.9",
]
```

### 7.2 线程模型（中控）

```
主线程 (Qt GUI)
  ├── UdpListenerThread (QThread)    → Signal → NodeManager
  ├── LogReceiverThread (QThread)    → Signal → LogInterface
  └── TcpCommandWorker (QThreadPool) → 并发发送 TCP 指令
```

### 7.3 被控端架构

纯 asyncio 单进程事件循环：HeartbeatService + CommandServer + LogReporter + ProcessManager。无 GUI 依赖。

### 7.4 打包

| 目标 | 产物 |
|------|------|
| 中控 | `Master.exe` (~30MB) |
| 被控 | `Slave.exe` (~15MB, 无 Qt) |

## 8. 测试策略

- `test_protocol.py`：协议解析/构建
- `test_node_manager.py`：节点状态机（上线、超时、分组）
- `test_account_pool.py`：账号分配逻辑
- 网络层用 mock socket
