"""GUI 配置：用户级设置持久化（%APPDATA%\\AIcut\\gui_config.json）。

与工程内 config/aicut.json 解耦：工程配置给管线脚本用，这里给桌面 GUI 用。
所有路径都有合理默认值，缺失不崩溃，设置页可改。
"""
from __future__ import annotations
from aicut.portability import portable_path

import json
import os
import sys
from pathlib import Path

APP_NAME = "AIcutOpenSource"
APP_DISPLAY = "AI 智能剪辑"
VERSION = "5.2.0"


def _default_project_root() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent)
    return str(Path(__file__).resolve().parent.parent)


def user_config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return user_config_dir() / "gui_config.json"


class GuiConfig:
    """轻量字典包装，带默认值与存取。"""

    DEFAULTS = {
        "project_root": _default_project_root(),
        "python_exe": "",          # Empty: use the current interpreter
        "ollama_host": "http://127.0.0.1:11434",
        "ollama_model": "gemma4:12b",
        "models_dir": str(Path(_default_project_root()) / "models"),
        "jianying_exe": "",
        "draft_root": str(Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"),
        "output_dir": "",          # 空则 <project_root>/07-output
        "theme": "dark",           # 旧字段，兼容 dark / light
        "gui_theme": "workbench",  # v5.0.0 皮肤：workbench(C 深色) / simple(B 浅色)
        "last_material_dir": "",
        "auto_style": True,
        "last_style_id": "koubo-zhishi",
        "voice": "zh-CN-XiaoxiaoNeural",
        "voice_rate": 0,
        "keyframes": "slow-zoom",
        "semantic": True,
        "knowledge": True,
        "publish": True,
        "copilot": False,          # v5.2.0：Copilot 方法论（Codex 蒸馏：子代理编排+偏好学习+断点续跑）
    }

    def __init__(self, data: dict):
        self._d = {**self.DEFAULTS, **(data or {})}

    @classmethod
    def load(cls) -> "GuiConfig":
        p = config_path()
        if p.exists():
            try:
                return cls(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                return cls({})
        return cls({})

    def save(self) -> None:
        config_path().write_text(
            json.dumps(self._d, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # —— 取值 ——
    def get(self, key: str, default=None):
        return self._d.get(key, default if default is not None else self.DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self._d[key] = value

    def as_dict(self) -> dict:
        return dict(self._d)

    # —— 派生路径 ——
    @property
    def project_root(self) -> Path:
        return Path(self._d.get("project_root") or _default_project_root())

    @property
    def lib_dir(self) -> Path:
        return self.project_root / "lib"

    @property
    def scripts_dir(self) -> Path:
        return self.project_root / "scripts"

    @property
    def content_library(self) -> Path:
        return self.project_root / "content-library"

    def python_exe(self) -> str:
        return self._d.get("python_exe") or sys.executable

    def output_dir(self) -> Path:
        o = self._d.get("output_dir")
        return Path(o) if o else (self.project_root / "07-output")

    def thumbnail_dir(self) -> Path:
        d = user_config_dir() / "thumbnails"
        d.mkdir(parents=True, exist_ok=True)
        return d


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_path(rel: str) -> str:
    """定位随包资源（冻结后在 _MEIPASS 下，源码在工程目录下）。"""
    if is_frozen():
        return str(Path(getattr(sys, "_MEIPASS", ".")) / rel)
    return str(Path(__file__).resolve().parent.parent / rel)


def app_icon_path() -> str:
    return resource_path("gui/assets/app.ico")
