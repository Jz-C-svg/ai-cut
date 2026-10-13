"""主题系统（v5.0.0）：两套完整皮肤。

- workbench  C 智能工作台（深色默认）：紫罗兰强调 + 青绿完成态
- simple     B 极简灵动（浅色切换）：品牌绿强调 + 大圆角白卡片

兼容：旧配置 theme="dark" 映射 workbench，theme="light" 映射 simple。
所有颜色走主题字典，QSS 不硬编码。
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# 调色板
# ---------------------------------------------------------------------------
# C 智能工作台（深色，默认）
WORKBENCH = {
    "bg": "#14141F",
    "bg2": "#1E1E2E",
    "bg3": "#2A2B38",
    "border": "#3A3B4D",
    "fg": "#E8E8F0",
    "fg2": "#C5C5D5",
    "muted": "#A0A0B8",
    "accent": "#722ED1",
    "accent2": "#9254DE",
    "ok": "#36CFC9",
    "warn": "#F5A623",
    "err": "#FF6B81",
    "chip": "#2F3040",
    "overlay": "rgba(20, 20, 31, 0.82)",
    "shadow": "rgba(0, 0, 0, 0.35)",
    "shot_a": "#5B4FCF",
    "shot_b": "#722ED1",
}

# B 极简灵动（浅色）
SIMPLE = {
    "bg": "#FAFBFC",
    "bg2": "#FFFFFF",
    "bg3": "#F0F1F5",
    "border": "#E5E7EB",
    "fg": "#1A1A1A",
    "fg2": "#333333",
    "muted": "#666666",
    "accent": "#0E9E94",
    "accent2": "#14B8A6",
    "ok": "#0E9E94",
    "warn": "#D97706",
    "err": "#DC2626",
    "chip": "#EAF7F5",
    "overlay": "rgba(255, 255, 255, 0.92)",
    "shadow": "rgba(15, 23, 42, 0.08)",
    "shot_a": "#99E6E0",
    "shot_b": "#5FD0C8",
}

# 旧版兼容（保留字典，构建时归一化到新皮肤）
DARK = dict(WORKBENCH)
LIGHT = dict(SIMPLE)


# ---------------------------------------------------------------------------
# 归一化与取色
# ---------------------------------------------------------------------------
def _normalize(name: str) -> str:
    n = (name or "workbench").lower()
    if n in ("workbench", "c", "dark"):
        return "workbench"
    if n in ("simple", "b", "light"):
        return "simple"
    return "workbench"


def palette(name: str) -> dict:
    return WORKBENCH if _normalize(name) == "workbench" else SIMPLE


def is_simple(name: str) -> bool:
    return _normalize(name) == "simple"


# ---------------------------------------------------------------------------
# QSS 构建
# ---------------------------------------------------------------------------
def build_qss(name: str) -> str:
    c = palette(name)
    return f"""
    QWidget {{
        background-color: {c['bg']};
        color: {c['fg2']};
        font-family: "Microsoft YaHei UI", "Microsoft YaHei", "等线", sans-serif;
        font-size: 13px;
    }}
    QMainWindow, QDialog {{ background-color: {c['bg']}; }}

    /* 顶部栏 / 菜单 */
    QMenuBar {{ background-color: {c['bg2']}; color: {c['fg2']}; }}
    QMenuBar::item:selected {{ background-color: {c['accent']}; color: white; }}
    QMenu {{ background-color: {c['bg2']}; color: {c['fg2']}; border: 1px solid {c['border']}; }}
    QMenu::item:selected {{ background-color: {c['accent']}; color: white; }}
    QToolBar {{ background-color: {c['bg2']}; border: 0px; border-bottom: 1px solid {c['border']}; spacing: 8px; padding: 6px 10px; }}
    QToolButton {{
        background-color: transparent; color: {c['fg2']};
        border: 1px solid transparent; border-radius: 8px; padding: 8px 12px;
    }}
    QToolButton:hover {{ background-color: {c['bg3']}; }}
    QToolButton:pressed {{ background-color: {c['accent']}; color: white; }}
    QToolButton:checked {{ background-color: {c['accent']}; color: white; }}

    /* 面板 / 卡片 */
    QGroupBox {{
        border: 1px solid {c['border']}; border-radius: 12px;
        margin-top: 14px; padding-top: 12px; color: {c['fg']}; background-color: {c['bg2']};
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
    QGroupBox:checked {{ border: 1px solid {c['accent']}; }}

    /* 页签 */
    QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; top: -1px; }}
    QTabBar::tab {{
        background: transparent; color: {c['muted']};
        padding: 8px 16px; border: none;
    }}
    QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; }}
    QTabBar::tab:hover {{ color: {c['fg']}; }}

    /* 输入控件 */
    QListWidget, QTreeWidget, QTableWidget, QTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {c['bg2']}; color: {c['fg2']};
        border: 1px solid {c['border']}; border-radius: 8px; padding: 6px;
        selection-background-color: {c['accent']}; selection-color: white;
    }}
    QListWidget {{ outline: 0; }}
    QListWidget::item {{ border-radius: 8px; padding: 4px; margin: 2px; }}
    QListWidget::item:selected {{ background-color: {c['accent']}; color: white; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{ background-color: {c['bg2']}; selection-background-color: {c['accent']}; border: 1px solid {c['border']}; }}

    /* 按钮 */
    QPushButton {{
        background-color: {c['bg3']}; color: {c['fg2']};
        border: 1px solid {c['border']}; border-radius: 10px; padding: 7px 16px;
    }}
    QPushButton:hover {{ background-color: {c['accent']}; color: white; border-color: {c['accent']}; }}
    QPushButton:disabled {{ color: {c['muted']}; background-color: {c['bg2']}; }}
    QPushButton#primary {{
        background-color: {c['accent']}; color: white; border: none;
        font-weight: bold; padding: 9px 20px; border-radius: 10px;
    }}
    QPushButton#primary:hover {{ background-color: {c['accent2']}; }}
    QPushButton#hero {{
        background-color: {c['accent']}; color: white; border: none;
        font-size: 16px; font-weight: bold; padding: 14px 40px; border-radius: 26px;
    }}
    QPushButton#hero:hover {{ background-color: {c['accent2']}; }}
    QPushButton#iconBtn {{
        background-color: transparent; border: 1px solid {c['border']};
        border-radius: 18px; font-size: 15px; padding: 4px 10px;
    }}
    QPushButton#iconBtn:hover {{ background-color: {c['bg3']}; }}

    /* 滑条 / 进度 */
    QSlider::groove:horizontal {{ height: 6px; background: {c['bg3']}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ background: {c['accent']}; width: 14px; margin: -5px 0; border-radius: 7px; }}
    QProgressBar {{
        background-color: {c['bg3']}; border: none;
        border-radius: 6px; text-align: center; color: {c['fg']}; height: 10px;
    }}
    QProgressBar::chunk {{ background-color: {c['ok']}; border-radius: 6px; }}

    /* 滚动条 */
    QScrollBar:vertical {{ background: transparent; width: 10px; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::horizontal {{ background: transparent; height: 10px; }}
    QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 5px; min-width: 24px; }}
    QSplitter::handle {{ background-color: transparent; }}
    QSplitter::handle:horizontal {{ width: 4px; }}
    QSplitter::handle:vertical {{ height: 4px; }}

    /* 文字角色 */
    QLabel#h1 {{ font-size: 16px; font-weight: bold; color: {c['fg']}; }}
    QLabel#h2 {{ font-size: 14px; font-weight: bold; color: {c['fg']}; }}
    QLabel#muted {{ color: {c['muted']}; }}
    QLabel#logo {{ font-size: 16px; font-weight: bold; color: {c['fg']}; }}
    QLabel#accent {{ color: {c['accent']}; font-weight: bold; }}
    QLabel#ok {{ color: {c['ok']}; font-weight: bold; }}
    QLabel#guide {{ font-size: 20px; color: {c['muted']}; }}

    /* 流水线灯 / 徽章 / AI 小窗等特殊控件（颜色由代码动态覆盖，这里只给底色） */
    QLabel#pipelineStep {{ color: {c['muted']}; padding: 2px 6px; }}
    QLabel#aiBadge {{
        background-color: {c['chip']}; color: {c['muted']};
        border-radius: 9px; padding: 2px 8px; font-size: 11px;
    }}
    QLabel#aiOverlay {{
        background-color: {c['overlay']}; color: {c['fg']};
        border: 1px solid {c['border']}; border-radius: 10px; padding: 6px;
    }}
    QLabel#markerDot {{ font-size: 15px; }}

    QStatusBar {{ background-color: {c['bg2']}; color: {c['fg2']}; border-top: 1px solid {c['border']}; }}
    QStatusBar::item {{ border: none; }}
    QCheckBox {{ color: {c['fg2']}; }}
    QCheckBox::indicator:checked {{ background-color: {c['accent']}; }}

    QToolTip {{
        background-color: {c['bg2']}; color: {c['fg']};
        border: 1px solid {c['accent']}; padding: 4px 8px;
    }}
    """
