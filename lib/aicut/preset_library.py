# -*- coding: utf-8 -*-
"""
preset_library.py — 预设素材库（花字/滤镜/特效/转场/BGM）
AI 选定风格后，自动返回该风格的全套预设，供草稿引擎添加。
用法：
  from aicut.preset_library import get_preset, list_presets
  p = get_preset("kadian-jinji")
  # p = {"滤镜":[...], "转场":[...], "人物特效":[...], "场景特效":[...], "花字":..., "BGM情绪":...}
"""
from __future__ import annotations
from aicut.portability import portable_path
import json, os

_ROOT = portable_path('project:')
_ROUTER = os.path.join(_ROOT, "content-library", "presets", "presets_router.json")


def _load():
    with open(_ROUTER, encoding="utf-8") as f:
        return json.load(f)


def list_presets() -> list[dict]:
    """返回所有风格预设 id/名称/数量。"""
    rt = _load()
    out = []
    for sid, v in rt["风格预设映射"].items():
        out.append({
            "id": sid, "名称": v["名称"],
            "滤镜数": len(v["滤镜"]), "转场数": len(v["转场"]),
            "人物特效数": len(v["人物特效"]), "场景特效数": len(v["场景特效"]),
            "花字": v["花字"], "BGM情绪": v["BGM情绪"],
        })
    return out


def get_preset(style_id: str) -> dict:
    """按风格 id 返回全套预设；找不到回退口播标准。"""
    rt = _load()
    p = rt["风格预设映射"].get(style_id)
    if p is None:
        p = rt["风格预设映射"].get("koubo-zhishi", {})
    bgm = rt.get("BGM情绪分类", {}).get(p.get("BGM情绪", ""), "")
    return {
        "风格": p.get("名称", ""),
        "滤镜": p.get("滤镜", []),
        "转场": p.get("转场", []),
        "人物特效": p.get("人物特效", []),
        "场景特效": p.get("场景特效", []),
        "花字": p.get("花字", ""),
        "BGM情绪": p.get("BGM情绪", ""),
        "BGM说明": bgm,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(json.dumps(get_preset(sys.argv[1]), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(list_presets(), ensure_ascii=False, indent=2))
