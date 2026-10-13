r"""感知灵敏度：所有阈值集中在这里，可调、可标定、可复现。

为什么单独抽出来：识别灵敏度是"剪得准不准"的上游。阈值散在各处时，调一次要改五个
文件、还没法比较。这里把真相放在 `config/perception.json`，代码里的默认值只是兜底。

三档预设（high/medium/low）：
  high   更敏感：更容易判"糊/过曝"、更容易抓到停顿（剪得狠，可能误杀）
  medium 默认：与 v2 首版一致
  low    更保守：只报明显的糊与停顿（剪得稳，可能漏）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import CONFIG_DIR

DEFAULTS: dict[str, Any] = {
    "preset": "medium",
    "scene_cut": 0.30,          # 镜头切点阈值：越小越敏感
    "sample_fps": 2.0,          # 抽帧采样率
    "blur_sensitivity": 1.0,
    "blur_abs_min": 60.0,       # 绝对下限：比它还糊才算"糊"（防自比自判误杀）
    "blur_abs_good": 260.0,     # 绝对下限：不够清晰就不叫"清晰"    # >1 更敏感：更多镜头被判"糊"、更少被判"清晰"
    "over_ratio": 0.08,         # 过曝像素占比超过它就标"过曝"
    "dark_ratio": 0.20,         # 欠曝像素占比超过它就标"欠曝"
    "motion_lively": 8.0,       # 帧间差超过它算"运动大"
    "skin_face": 0.02,          # 肤色占比超过它才算"有主体"
    "silence_db": -30.0,        # 静音判定阈值（dB）
    "silence_min_len": 0.5,     # 最短静音长度（秒）
    "pad": 0.15,                # 删除区间两侧留白（秒），口播素材可调大到 0.25
    "adapt": {                  # 某步没命中时的放宽档
        "delete_silences": {"min_len": 0.35, "db": -35.0},
        "delete_ai_cuts": {"min_ratio": 0.45},
    },
    "note": "",
}

PRESETS: dict[str, dict[str, Any]] = {
    "high": {"scene_cut": 0.22, "blur_sensitivity": 1.25, "over_ratio": 0.05,
             "dark_ratio": 0.15, "motion_lively": 6.0, "skin_face": 0.01,
             "silence_db": -35.0, "silence_min_len": 0.4,
             "adapt": {"delete_silences": {"min_len": 0.30, "db": -38.0},
                       "delete_ai_cuts": {"min_ratio": 0.40}}},
    "medium": {"scene_cut": 0.30, "blur_sensitivity": 1.0,
    "blur_abs_min": 60.0,       # 绝对下限：比它还糊才算"糊"（防自比自判误杀）
    "blur_abs_good": 260.0,     # 绝对下限：不够清晰就不叫"清晰" "over_ratio": 0.08,
               "dark_ratio": 0.20, "motion_lively": 8.0, "skin_face": 0.02,
               "silence_db": -30.0, "silence_min_len": 0.5,
               "adapt": DEFAULTS["adapt"]},
    "low": {"scene_cut": 0.40, "blur_sensitivity": 0.80, "over_ratio": 0.12,
            "dark_ratio": 0.28, "motion_lively": 11.0, "skin_face": 0.03,
            "silence_db": -27.0, "silence_min_len": 0.7,
            "adapt": {"delete_silences": {"min_len": 0.45, "db": -30.0},
                      "delete_ai_cuts": {"min_ratio": 0.55}}},
    # 口播素材预设（参考 AI驯剪 run.mjs 气口参数）：更狠地抓停顿、多留呼吸
    "koubo": {"scene_cut": 0.30, "blur_sensitivity": 1.0, "over_ratio": 0.08,
              "dark_ratio": 0.20, "motion_lively": 8.0, "skin_face": 0.02,
              "silence_db": -38.0, "silence_min_len": 0.6, "pad": 0.25,
              "adapt": {"delete_silences": {"min_len": 0.40, "db": -42.0},
                        "delete_ai_cuts": {"min_ratio": 0.45}}},
}

_CACHE: dict[str, Any] | None = None


def config_path() -> Path:
    return CONFIG_DIR / "perception.json"


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


def set_value(key: str, value: Any) -> dict[str, Any]:
    """内存里改（用于灵敏度扫描，不落盘）。"""
    data = dict(load())
    data[key] = value
    global _CACHE
    _CACHE = data
    return data


def apply_preset(name: str) -> dict[str, Any]:
    if name not in PRESETS:
        raise KeyError(f"未知档位 {name}，可选 {list(PRESETS)}")
    data = dict(DEFAULTS)
    data.update(PRESETS[name])
    data["preset"] = name
    global _CACHE
    _CACHE = data
    return data


def save(note: str = "") -> Path:
    data = dict(load())
    if note:
        data["note"] = note
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(".json.bak")
        backup.write_text(path.read_text(encoding="utf-8-sig"), encoding="utf-8")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
