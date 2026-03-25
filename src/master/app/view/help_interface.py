"""帮助页面 — 视觉化帮助系统（适配中老年用户）

设计原则：
- 大字体、高对比度、充足留白
- 原生 Qt 卡片组件（StepCard / FaqCard）代替纯 HTML
- 功能说明和更新日志保留增强 Markdown 渲染
"""
from __future__ import annotations

from pathlib import Path

import markdown
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    IconWidget,
    Pivot,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    Theme,
    qconfig,
)
from qfluentwidgets import FluentIcon as FIF

from master.app.common.config import RESOURCE_DIR

# ════════════════════════════════════════════════════════════
# 快速入门 — 步骤数据
# ════════════════════════════════════════════════════════════

_STEPS: list[dict[str, object]] = [
    {
        "icon": FIF.PLAY,
        "title": "启动主控",
        "desc": (
            "双击 <b>TriangleAlpha-Master.exe</b> 启动群控中心。<br>"
            "首次启动会在用户目录下自动创建配置文件夹。"
        ),
    },
    {
        "icon": FIF.DOWNLOAD,
        "title": "部署被控端",
        "desc": (
            "在每台挂机虚拟机上执行以下操作：<br>"
            "① 将 <b>TriangleAlpha-Slave.exe</b> 放入脚本同目录<br>"
            "② 只运行 <b>TriangleAlpha-Slave.exe</b>，程序会自动准备 <b>SlaveClientConsole.exe</b> 兼容占位文件<br>"
            "③ 新建 <b>主控IP.txt</b>，写入主控机器的 IP 地址<br>"
            "④ 后续始终双击 <b>TriangleAlpha-Slave.exe</b>，节点会自动出现在主控"
        ),
    },
    {
        "icon": FIF.PEOPLE,
        "title": "导入账号",
        "desc": (
            "在 <b>大屏模式</b> 点击「上传账号」按钮。<br><br>"
            "格式（每行一个）：<br>"
            "<code>账号----密码</code><br>"
            "<code>账号----密码----邮箱----邮箱密码----备注</code><br><br>"
            "其中邮箱、邮箱密码、备注可以省略。"
        ),
    },
    {
        "icon": FIF.CERTIFICATE,
        "title": "配置验证码",
        "desc": (
            "在 <b>大屏模式 → 验证码</b> 页签：<br>"
            "① 点击「前往充值」完成验证码余额充值<br>"
            "② 将 API Key 粘贴到输入框<br>"
            "③ 点击「<b>保存并下发</b>」— Key 会推送到所有节点"
        ),
    },
    {
        "icon": FIF.SYNC,
        "title": "下发配置",
        "desc": (
            "在 <b>大屏模式 → 配置</b> 页签：<br>"
            "设置补齐队友、武器、下号等级、舔包次数，<br>"
            "然后点击「<b>下发配置</b>」推送到全部在线节点。<br><br>"
            "💡 选中特定节点后，仅对选中节点生效。"
        ),
    },
    {
        "icon": FIF.PLAY_SOLID,
        "title": "启动挂机",
        "desc": (
            "在 <b>大屏模式 → 操作</b> 页签，<br>"
            "点击「<b>启动/重启脚本</b>」。<br><br>"
            "节点会自动请求账号，主控自动分配空闲账号。<br>"
            "等级达标后自动换号，无需人工干预。"
        ),
    },
    {
        "icon": FIF.COMPLETED,
        "title": "提取成果",
        "desc": (
            "账号完成后（状态显示「已完成」），<br>"
            "在 <b>文件</b> 页签点击「<b>提取账号</b>」导出到文件。<br><br>"
            "💡 如果开启了分销平台（设置页），<br>"
            "已完成账号会自动上传，无需手动操作。"
        ),
    },
]

# ════════════════════════════════════════════════════════════
# FAQ — 问答数据
# ════════════════════════════════════════════════════════════

_FAQ_DATA: list[dict[str, str]] = [
    {
        "q": "被控端启动后，主控看不到节点",
        "a": (
            "<b>按以下顺序逐一排查：</b><br><br>"
            "① 被控端目录下是否有 <b>主控IP.txt</b>？<br>"
            "　　文件内容必须是主控机器的 IP 地址<br><br>"
            "② 主控的 <b>UDP 端口 8888</b> 是否被防火墙拦截？<br>"
            "　　请在主控机器上放行此端口<br><br>"
            "③ 两台机器能否互相 <b>ping</b> 通？<br>"
            "　　如果 ping 不通，说明网络不通<br><br>"
            "④ 如果使用 Tailscale 等内网穿透，<br>"
            "　　确认填写的是 <b>Tailscale IP</b>，不是本机 IP"
        ),
    },
    {
        "q": "节点显示「在线」但脚本不启动",
        "a": (
            "① 只运行 <b>TriangleAlpha-Slave.exe</b><br>"
            "　　<b>SlaveClientConsole.exe</b> 只是自动生成的兼容占位文件，不要手动双击<br><br>"
            "② 检查 TestDemo 脚本本身是否正常<br><br>"
            "③ 尝试在节点上 <b>右键 → 启动/重启脚本</b>"
        ),
    },
    {
        "q": "运行状态显示 ⚠缺Key 或 ⚠Key不一致",
        "a": (
            "说明该节点缺少验证码 Key 或者 Key 和主控不一样。<br><br>"
            "通常主控会自动修复。如未修复：<br>"
            "① 前往 <b>验证码</b> 页签，确认 Key 正确<br>"
            "② 点击「<b>保存并下发</b>」手动推送"
        ),
    },
    {
        "q": "验证码余额查询失败",
        "a": (
            "① 确认 API Key 已正确输入且已充值<br>"
            "② 检查主控机器的网络连接是否正常<br>"
            "③ 余额服务可能临时不可用，等几分钟再试"
        ),
    },
    {
        "q": "账号导入后数量不对",
        "a": (
            "⚠️ 格式必须是 <b>账号----密码</b>（四个短横线分隔）<br>"
            "　　不能用逗号、空格或其他分隔符<br><br>"
            "💡 重复的账号会自动跳过（按用户名去重）<br>"
            "💡 空行也会被自动忽略"
        ),
    },
    {
        "q": "等级和金币数据不更新",
        "a": (
            "数据通过被控端每 <b>3 秒</b>上报一次。<br><br>"
            "如果长时间不更新：<br>"
            "① 检查被控端是否还在正常运行<br>"
            "② 脚本停止时数据会保留最后值，不会清零"
        ),
    },
    {
        "q": "如何只操作部分节点？",
        "a": (
            "在节点表格中：<br>"
            "• <b>Ctrl + 点击</b> 可多选节点<br>"
            "• <b>Shift + 点击</b> 可选择连续范围<br><br>"
            "选中后，所有操作按钮（启动/停止/下发等）<br>"
            "都只会对 <b>选中的节点</b> 生效。<br><br>"
            "💡 不选中任何节点时，操作会作用于全部在线节点。"
        ),
    },
    {
        "q": "配置下发后节点没有生效",
        "a": (
            "① 确认节点状态是「在线」（绿色圆点）<br>"
            "② 确认 TCP 指令端口（默认 9999）没被占用<br>"
            "③ 尝试 <b>重启脚本</b> 使新配置生效"
        ),
    },
    {
        "q": "分销平台怎么用？",
        "a": (
            "① 进入 <b>设置 → 分销平台</b><br>"
            "② 打开启用开关<br>"
            "③ 填写 API 地址、用户名、密码、分组名<br><br>"
            "开启后，已完成的账号会在几秒内自动上传。<br>"
            "平台取号后，本地状态自动变为「已取号」。<br><br>"
            "⚠️ 连续登录失败 3 次会自动暂停，请检查密码。"
        ),
    },
    {
        "q": "超时监控是什么？",
        "a": (
            "在大屏模式底部，勾选「<b>超时监控</b>」并设定分钟数。<br><br>"
            "如果某个节点超过设定时间没有状态更新，<br>"
            "主控会 <b>自动重启</b> 该节点的脚本。<br><br>"
            "💡 适合处理脚本卡死、游戏崩溃等异常情况。"
        ),
    },
    {
        "q": "怎么备份账号数据？",
        "a": (
            "账号数据保存在主控 exe 同目录的<br>"
            "<b>accounts.db</b> 文件中。<br><br>"
            "直接 <b>复制这个文件</b> 即可备份。<br>"
            "恢复时把备份文件放回原位即可。"
        ),
    },
    {
        "q": "主控最多能管多少个节点？",
        "a": (
            "没有硬性限制，实测 <b>50+ 节点</b>稳定运行。<br><br>"
            "节点表格使用增量更新技术，<br>"
            "节点多了也不会卡顿。"
        ),
    },
]


# ════════════════════════════════════════════════════════════
# 自定义组件
# ════════════════════════════════════════════════════════════


class _NumberBadge(QLabel):
    """圆形编号徽章 — 大号数字，蓝色圆底白字"""

    def __init__(self, number: int, parent: QWidget | None = None) -> None:
        super().__init__(str(number), parent)
        self.setFixedSize(44, 44)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(QFont("Segoe UI", 17, QFont.Weight.Bold))
        self._applyTheme()
        qconfig.themeChanged.connect(self._applyTheme)

    def _applyTheme(self) -> None:
        bg = "#0078d4" if qconfig.theme != Theme.DARK else "#4cc2ff"
        self.setStyleSheet(
            f"background-color: {bg}; color: white; border-radius: 22px;"
        )


class _StepCard(CardWidget):
    """编号步骤卡片：左侧数字徽章 + 右侧标题和描述"""

    def __init__(
        self, number: int, icon: FIF, title: str, desc: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setBorderRadius(10)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(20)

        # 左：编号徽章
        badge = _NumberBadge(number, self)
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        # 右：标题 + 描述
        right = QVBoxLayout()
        right.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        icon_w = IconWidget(icon, self)
        icon_w.setFixedSize(22, 22)
        title_row.addWidget(icon_w)
        title_lbl = SubtitleLabel(title, self)
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        right.addLayout(title_row)

        desc_lbl = BodyLabel(self)
        desc_lbl.setWordWrap(True)
        desc_lbl.setTextFormat(Qt.TextFormat.RichText)
        desc_lbl.setText(desc)
        right.addWidget(desc_lbl)

        layout.addLayout(right, 1)


class _FaqCard(CardWidget):
    """可折叠 FAQ 卡片 — 点击展开/收起答案"""

    def __init__(
        self, question: str, answer_html: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._expanded = False
        self._question = question
        self.setBorderRadius(10)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(0)

        # 问题行
        q_row = QHBoxLayout()
        q_row.setSpacing(12)

        q_badge = QLabel("Q", self)
        q_badge.setFixedSize(28, 28)
        q_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        q_badge.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        q_badge.setStyleSheet(
            "background-color: #0078d4; color: white; border-radius: 14px;"
        )
        q_row.addWidget(q_badge, 0, Qt.AlignmentFlag.AlignTop)

        self._headerLabel = StrongBodyLabel(question, self)
        self._headerLabel.setWordWrap(True)
        q_row.addWidget(self._headerLabel, 1)

        self._arrow = QLabel("▶", self)
        self._arrow.setFixedWidth(20)
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        q_row.addWidget(self._arrow, 0, Qt.AlignmentFlag.AlignTop)

        layout.addLayout(q_row)

        # 分隔线
        self._sep = QFrame(self)
        self._sep.setFrameShape(QFrame.Shape.HLine)
        self._sep.setStyleSheet("color: rgba(128,128,128,0.3);")
        self._sep.setVisible(False)
        layout.addSpacing(8)
        layout.addWidget(self._sep)

        # 答案
        self._answer = QLabel(self)
        self._answer.setWordWrap(True)
        self._answer.setTextFormat(Qt.TextFormat.RichText)
        self._answer.setStyleSheet("font-size: 14px; line-height: 1.6; padding: 4px 0 4px 40px;")
        self._answer.setText(answer_html)
        self._answer.setVisible(False)
        layout.addSpacing(4)
        layout.addWidget(self._answer)

    def mouseReleaseEvent(self, e) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(e)
        self._expanded = not self._expanded
        self._arrow.setText("▼" if self._expanded else "▶")
        self._sep.setVisible(self._expanded)
        self._answer.setVisible(self._expanded)


# ════════════════════════════════════════════════════════════
# 主界面
# ════════════════════════════════════════════════════════════

_TABS: list[tuple[str, str]] = [
    ("guide", "功能说明"),
    ("quickstart", "快速入门"),
    ("faq", "常见问题"),
    ("changelog", "更新日志"),
]


class HelpInterface(ScrollArea):
    """帮助页面 — Pivot 标签切换，混合渲染（原生组件 + 增强 HTML）"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("helpInterface")

        self.scrollWidget = QWidget(self)
        self.scrollWidget.setObjectName("view")
        self.vBoxLayout = QVBoxLayout(self.scrollWidget)
        self.vBoxLayout.setContentsMargins(36, 0, 36, 0)

        # 标题
        self.helpLabel = QLabel("帮助", self)
        self.helpLabel.setObjectName("helpLabel")

        # Pivot + Stack
        self.pivot = Pivot(self)
        self.stackedWidget = QStackedWidget(self)

        self._initWidget()
        self._initLayout()
        self._loadTabs()
        qconfig.themeChanged.connect(self._onThemeChanged)

    # ── 初始化 ──

    def _initWidget(self) -> None:
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 140, 0, 5)
        self.helpLabel.move(36, 30)
        self.helpLabel.setStyleSheet(
            "font: 33px 'Segoe UI', 'Microsoft YaHei'; font-weight: bold;"
        )
        self.pivot.move(40, 80)

    def _initLayout(self) -> None:
        self.vBoxLayout.addWidget(self.stackedWidget)
        self.vBoxLayout.addStretch()

    def _loadTabs(self) -> None:
        builders = [
            self._buildGuideTab,
            self._buildQuickstartTab,
            self._buildFaqTab,
            self._buildChangelogTab,
        ]
        for i, ((key, name), builder) in enumerate(zip(_TABS, builders, strict=True)):
            widget = builder()
            widget.setObjectName(key)
            self.stackedWidget.addWidget(widget)
            self.pivot.addItem(
                routeKey=key, text=name,
                onClick=lambda idx=i: self._onTabChanged(idx),
            )
        self.pivot.setCurrentItem(_TABS[0][0])
        self.stackedWidget.setCurrentIndex(0)

    # ── Tab 切换 ──

    def _onTabChanged(self, index: int) -> None:
        self.stackedWidget.setCurrentIndex(index)
        self.verticalScrollBar().setValue(0)
        current = self.stackedWidget.currentWidget()
        if current:
            hint = current.sizeHint().height()
            self.stackedWidget.setFixedHeight(max(hint + 40, 400))

    # ── Tab 1: 功能说明（增强 HTML）──

    def _buildGuideTab(self) -> QLabel:
        label = QLabel(self.stackedWidget)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setOpenExternalLinks(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(self._renderMarkdown(RESOURCE_DIR / "docs" / "help" / "guide.md"))
        return label

    # ── Tab 2: 快速入门（原生步骤卡片）──

    def _buildQuickstartTab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 12, 0, 20)
        layout.setSpacing(12)

        # 引导语
        intro = BodyLabel("按照以下 7 个步骤，从零开始完成自动挂机的全部配置：", container)
        intro.setStyleSheet("font-size: 15px; color: gray; padding: 0 0 8px 4px;")
        layout.addWidget(intro)

        for i, step in enumerate(_STEPS):
            card = _StepCard(
                i + 1, step["icon"], step["title"], step["desc"],  # type: ignore[arg-type]
                parent=container,
            )
            layout.addWidget(card)

        # 底部提示
        tip = QLabel(
            '<div style="background: rgba(0,120,212,0.08); border-left: 4px solid #0078d4; '
            'padding: 12px 16px; border-radius: 4px; font-size: 14px;">'
            "💡 <b>操作小贴士</b><br>"
            "• 节点卡住 → 右键该节点 → 停止脚本 → 启动脚本<br>"
            "• 更换武器 → 配置页签 → 选择武器 → 下发配置<br>"
            "• 查看余额 → 验证码页签 → 点击查询"
            "</div>",
            container,
        )
        tip.setWordWrap(True)
        tip.setTextFormat(Qt.TextFormat.RichText)
        layout.addSpacing(8)
        layout.addWidget(tip)

        layout.addStretch()
        return container

    # ── Tab 3: FAQ（可折叠卡片）──

    def _buildFaqTab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 12, 0, 20)
        layout.setSpacing(10)

        intro = BodyLabel("点击问题查看解答：", container)
        intro.setStyleSheet("font-size: 15px; color: gray; padding: 0 0 8px 4px;")
        layout.addWidget(intro)

        for item in _FAQ_DATA:
            card = _FaqCard(item["q"], item["a"], parent=container)
            layout.addWidget(card)

        layout.addStretch()
        return container

    # ── Tab 4: 更新日志（增强 HTML）──

    def _buildChangelogTab(self) -> QLabel:
        label = QLabel(self.stackedWidget)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setOpenExternalLinks(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(self._renderMarkdown(RESOURCE_DIR / "docs" / "help" / "changelog.md"))
        return label

    # ── 主题变更 ──

    def _onThemeChanged(self) -> None:
        # 刷新 HTML 渲染的 Tab（Guide + Changelog）
        guide = self.stackedWidget.widget(0)
        if isinstance(guide, QLabel):
            guide.setText(self._renderMarkdown(RESOURCE_DIR / "docs" / "help" / "guide.md"))
        changelog = self.stackedWidget.widget(3)
        if isinstance(changelog, QLabel):
            changelog.setText(self._renderMarkdown(RESOURCE_DIR / "docs" / "help" / "changelog.md"))

    # ── Markdown 渲染 ──

    def _renderMarkdown(self, md_path: Path) -> str:
        try:
            md_text = md_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            md_text = "> 帮助内容暂不可用，请重新安装应用。"

        html = markdown.markdown(md_text, extensions=["tables", "fenced_code"])

        # H2 后加 <hr>
        html = html.replace("</h2>", "</h2><hr>")

        # 💡 提示 callout（绿色）
        html = html.replace(
            "<blockquote>\n<p>💡",
            '<div style="background: rgba(76,175,80,0.1); border-left: 4px solid #4caf50; '
            'padding: 12px 16px; margin: 10px 0; border-radius: 4px;">\n<p>💡',
        )
        # ⚠️ 警告 callout（橙色）
        html = html.replace(
            "<blockquote>\n<p>⚠️",
            '<div style="background: rgba(255,152,0,0.12); border-left: 4px solid #ff9800; '
            'padding: 12px 16px; margin: 10px 0; border-radius: 4px;">\n<p>⚠️',
        )
        # 关闭对应的 </blockquote> → </div>（简单替换可能影响其他 blockquote，但此场景可控）
        html = html.replace("</blockquote>", "</div>")

        css = self._buildCss()
        return f"<style>{css}</style>{html}"

    def _buildCss(self) -> str:
        is_dark = qconfig.theme == Theme.DARK
        text = "#e0e0e0" if is_dark else "#1a1a1a"
        border = "rgba(255,255,255,0.15)" if is_dark else "rgba(0,0,0,0.12)"
        code_bg = "rgba(255,255,255,0.06)" if is_dark else "rgba(0,0,0,0.04)"
        hr_color = "rgba(255,255,255,0.1)" if is_dark else "rgba(0,0,0,0.08)"
        h2_border = "#4cc2ff" if is_dark else "#0078d4"
        table_header_bg = "rgba(0,120,212,0.1)" if is_dark else "rgba(0,120,212,0.06)"
        table_alt_bg = "rgba(255,255,255,0.03)" if is_dark else "rgba(0,0,0,0.02)"

        return f"""
            body {{
                color: {text};
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                font-size: 15px; line-height: 1.9;
            }}
            h1 {{ font-size: 26px; margin: 20px 0 10px; font-weight: bold; }}
            h2 {{
                font-size: 21px; margin: 24px 0 8px; font-weight: bold;
                border-left: 4px solid {h2_border}; padding-left: 12px;
            }}
            h3 {{ font-size: 17px; margin: 16px 0 6px; font-weight: bold; }}
            h4 {{ font-size: 15px; font-weight: bold; margin: 12px 0 4px; }}
            hr {{
                border: none; border-top: 1px solid {hr_color};
                margin: 6px 0 16px;
            }}
            table {{
                border-collapse: collapse; width: 100%; margin: 10px 0;
            }}
            th {{
                border: 1px solid {border}; padding: 8px 14px;
                text-align: left; font-weight: bold;
                background: {table_header_bg};
            }}
            td {{
                border: 1px solid {border}; padding: 8px 14px;
                text-align: left;
            }}
            tr:nth-child(even) td {{ background: {table_alt_bg}; }}
            code {{
                background: {code_bg}; padding: 2px 6px;
                border-radius: 3px; font-family: Consolas, monospace;
            }}
            pre {{
                background: {code_bg}; padding: 14px;
                border-radius: 6px; overflow-x: auto;
            }}
            ul, ol {{ padding-left: 24px; }}
            li {{ margin: 5px 0; }}
            blockquote {{
                border-left: 4px solid {h2_border};
                padding: 10px 16px; margin: 10px 0;
                background: rgba(0,120,212,0.06);
                border-radius: 4px;
            }}
            a {{ color: {h2_border}; font-weight: bold; }}
        """
