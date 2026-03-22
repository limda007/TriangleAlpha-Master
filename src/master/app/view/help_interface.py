"""帮助页面 — 功能说明/快速入门/FAQ/更新日志"""
from __future__ import annotations

from pathlib import Path

import markdown
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import Pivot, ScrollArea, Theme, qconfig

from master.app.common.config import RESOURCE_DIR

_TABS: list[tuple[str, str, str]] = [
    ("guideLabel", "guide.md", "功能说明"),
    ("quickstartLabel", "quickstart.md", "快速入门"),
    ("faqLabel", "faq.md", "FAQ"),
    ("changelogLabel", "changelog.md", "更新日志"),
]


class HelpInterface(ScrollArea):
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

        # Pivot Tab
        self.pivot = Pivot(self)
        self.stackedWidget = QStackedWidget(self)

        # 每个 Tab 对应一个 QLabel
        for attr, _, _ in _TABS:
            label = QLabel(self.stackedWidget)
            setattr(self, attr, label)

        self._initWidget()
        self._initLayout()
        self._loadContent()
        self._connectSignals()

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

        for attr, _, _ in _TABS:
            label: QLabel = getattr(self, attr)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            label.setOpenExternalLinks(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            label.setTextFormat(Qt.TextFormat.RichText)

    def _initLayout(self) -> None:
        self.vBoxLayout.addWidget(self.stackedWidget)
        self.vBoxLayout.addStretch()

    def _loadContent(self) -> None:
        docs_dir = RESOURCE_DIR / "docs" / "help"
        for i, (attr, filename, tab_name) in enumerate(_TABS):
            label: QLabel = getattr(self, attr)
            label.setText(self._renderMarkdown(docs_dir / filename))
            self.stackedWidget.addWidget(label)
            self.pivot.addItem(
                routeKey=attr,
                text=tab_name,
                onClick=lambda idx=i: self._onTabChanged(idx),
            )
        self.pivot.setCurrentItem(_TABS[0][0])
        self.stackedWidget.setCurrentIndex(0)

    def _connectSignals(self) -> None:
        qconfig.themeChanged.connect(self._onThemeChanged)
        self.stackedWidget.currentChanged.connect(
            lambda: self.verticalScrollBar().setValue(0)
        )

    # ── Tab / 主题 ──

    def _onTabChanged(self, index: int) -> None:
        self.stackedWidget.setCurrentIndex(index)
        self.verticalScrollBar().setValue(0)
        current = self.stackedWidget.currentWidget()
        if current:
            self.stackedWidget.setFixedHeight(current.sizeHint().height() + 20)

    def _onThemeChanged(self) -> None:
        docs_dir = RESOURCE_DIR / "docs" / "help"
        for attr, filename, _ in _TABS:
            label: QLabel = getattr(self, attr)
            label.setText(self._renderMarkdown(docs_dir / filename))

    # ── Markdown 渲染 ──

    def _renderMarkdown(self, md_path: Path) -> str:
        try:
            md_text = md_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            md_text = "> 帮助内容暂不可用，请重新安装应用。"

        html_body = markdown.markdown(
            md_text,
            extensions=["tables", "fenced_code"],
        )
        html_body = html_body.replace("</h2>", "</h2><hr>")
        css = self._buildCss()
        return f"<style>{css}</style>{html_body}"

    def _buildCss(self) -> str:
        is_dark = qconfig.theme == Theme.DARK
        text_color = "#e0e0e0" if is_dark else "#1a1a1a"
        border_color = "rgba(255,255,255,0.15)" if is_dark else "rgba(0,0,0,0.15)"
        code_bg = "rgba(255,255,255,0.06)" if is_dark else "rgba(0,0,0,0.04)"
        hr_color = "rgba(255,255,255,0.1)" if is_dark else "rgba(0,0,0,0.1)"

        return f"""
            body {{ color: {text_color}; font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                   font-size: 14px; line-height: 1.8; }}
            h1 {{ font-size: 24px; margin: 16px 0 8px; }}
            h2 {{ font-size: 20px; margin: 16px 0 8px; }}
            h3 {{ font-size: 16px; margin: 12px 0 6px; }}
            h4 {{ font-size: 14px; font-weight: bold; margin: 10px 0 4px; }}
            hr {{ border: none; border-top: 1px solid {hr_color}; margin: 8px 0 16px; }}
            table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
            th, td {{ border: 1px solid {border_color}; padding: 6px 12px; text-align: left; }}
            th {{ font-weight: bold; }}
            code {{ background: {code_bg}; padding: 2px 6px; border-radius: 3px; font-family: Consolas, monospace; }}
            pre {{ background: {code_bg}; padding: 12px; border-radius: 4px; overflow-x: auto; }}
            ul, ol {{ padding-left: 24px; }}
            li {{ margin: 4px 0; }}
        """
