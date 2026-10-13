r"""界面动作的速度与等待参数（可标定）。

分两类：
  1. 运动速度：鼠标移动的插值档位。instant=瞬移（1 步）、fast=6 步、normal=20 步。
     数值越小越快，但"看起来像人"的程度越高——取决于你要速度还是要自然。
  2. 等待时间：按键后、点击后、翻页后要等多久再读状态。这个必须用实测的"界面稳定
     时间"来定，猜 0.02s 会读到旧状态，猜 3s 又白等。

真相文件：config/ui_speed.json（由 scripts/tune_speed.py 实测写入）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import CONFIG_DIR

DEFAULTS: dict[str, Any] = {
    "move_speed": "instant",     # instant | fast | normal
    "click_speed": "instant",
    "key_delay_s": 0.05,
    "post_move_ms": 8,           # 移动后、按下前留几毫秒（避免点到旧位置）
    "click_press_ms": 6,         # 按下到抬起之间留几毫秒         # 发键后等多久
    "action_settle_s": 0.25,     # 单个动作后等界面稳定
    "page_settle_s": 1.2,        # 返回首页/切面板后等界面稳定
    "open_draft_settle_s": 4.0, # 打开草稿到编辑页就绪（实测 3.7-4.2s）        # 翻页/打开草稿后等界面稳定
    "measured": {},              # tune_speed.py 实测到的原始数字
    "note": "",
}

_CACHE: dict[str, Any] | None = None


def config_path() -> Path:
    return CONFIG_DIR / "ui_speed.json"


def load(force: bool = False) -> dict[str, Any]:
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    data = dict(DEFAULTS)
    path = config_path()
    if path.exists():
        try:
            disk = json.loads(path.read_text(encoding="utf-8-sig"))
            data.update({k: v for k, v in disk.items() if k in DEFAULTS})
        except ValueError:
            pass
    _CACHE = data
    return data


def get(key: str, default: Any = None) -> Any:
    return load().get(key, DEFAULTS.get(key, default))


def save(patch: dict, note: str = "") -> Path:
    data = dict(load())
    data.update({k: v for k, v in patch.items() if k in DEFAULTS})
    if note:
        data["note"] = note
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(".json.bak").write_text(path.read_text(encoding="utf-8-sig"), encoding="utf-8")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    global _CACHE
    _CACHE = data
    return path
