r"""文字加工：字体、字号、颜色、描边、位置、入场动画与关键词局部高亮。

剪映的文字样式存在 text 素材的 content（字符串化的 JSON）里，styles 是一个数组，
每个元素带 range 字段，因此可以在同一句字幕里对部分文字单独着色（关键词高亮）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from JianYingDraft import (  # noqa: E402
    ClipSettings, FontType, TextBorder, TextStyle,
    TextIntro, TextOutro, TextLoopAnim,
)

PRESET_FILE = ROOT / "config" / "text_styles.json"

# 数字：阿拉伯数字、百分号、小数点、常见量词
NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|秒|分钟|小时|天|周|个月|年|次|遍|个|条|斤|克|升|毫升|度|cm|CM|mm|米|公里)?")


def load_presets() -> dict:
    return json.loads(PRESET_FILE.read_text(encoding="utf-8"))["presets"]


def font(name: str | None) -> FontType | None:
    if not name:
        return None
    member = getattr(FontType, name, None)
    if member is None:
        raise KeyError(f"字体不存在: {name}")
    return member


def style_of(cfg: dict) -> TextStyle:
    return TextStyle(
        size=float(cfg.get("字号", 6.0)),
        bold=bool(cfg.get("加粗", False)),
        italic=bool(cfg.get("斜体", False)),
        underline=bool(cfg.get("下划线", False)),
        color=tuple(cfg.get("颜色", [1.0, 1.0, 1.0])),
        alpha=float(cfg.get("透明度", 1.0)),
        align=int(cfg.get("对齐", 1)),
        letter_spacing=int(cfg.get("字符间距", 0)),
        line_spacing=int(cfg.get("行间距", 0)),
        auto_wrapping=True,
    )


def border_of(cfg: dict) -> TextBorder | None:
    b = cfg.get("描边")
    if not b:
        return None
    return TextBorder(
        color=tuple(b.get("颜色", [0.0, 0.0, 0.0])),
        width=float(b.get("宽度", 40)),
        alpha=float(b.get("透明度", 1.0)),
    )


def clip_of(cfg: dict) -> ClipSettings:
    return ClipSettings(transform_y=float(cfg.get("位置", -0.8)))


def animation_of(cfg: dict, kind: str = "入场动画"):
    """把预设里的中文动画名解析成插件枚举。"""
    name = cfg.get(kind)
    if not name:
        return None
    for enum_cls in (TextIntro, TextOutro, TextLoopAnim):
        member = getattr(enum_cls, name, None)
        if member is not None:
            return member
    raise KeyError(f"文字动画不存在: {name}")


def add_animation_to(segment, cfg: dict) -> None:
    name = cfg.get("入场动画")
    if name:
        segment.add_animation(animation_of(cfg, "入场动画"), duration=f"{cfg.get('入场时长', 0.5)}s")
    name = cfg.get("出场动画")
    if name:
        segment.add_animation(animation_of(cfg, "出场动画"), duration=f"{cfg.get('出场时长', 0.5)}s")


def keyword_ranges(text: str, rules: list[str], custom: list[str] | None = None) -> list[tuple[int, int]]:
    """给出需要高亮的字符区间（按字符下标，含头不含尾）。"""
    spans: list[tuple[int, int]] = []
    if "数字" in rules:
        spans += [(m.start(), m.end()) for m in NUMBER_PATTERN.finditer(text)]
    if "关键词" in rules or custom:
        for word in (custom or []):
            start = 0
            while True:
                idx = text.find(word, start)
                if idx < 0:
                    break
                spans.append((idx, idx + len(word)))
                start = idx + len(word)
    # 合并重叠区间
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def apply_highlights(draft_dir: Path, highlight_cfg: dict, keywords: list[str] | None = None) -> int:
    """给草稿里的每句字幕注入关键词高亮样式，返回处理的文本素材数量。"""
    content_path = draft_dir / "draft_content.json"
    data = json.loads(content_path.read_text(encoding="utf-8"))
    color = tuple(highlight_cfg.get("颜色", [1.0, 0.83, 0.0]))
    bold = bool(highlight_cfg.get("加粗", True))
    rules = highlight_cfg.get("规则", ["数字"])
    touched = 0

    for material in data.get("materials", {}).get("texts", []):
        raw = material.get("content")
        if not isinstance(raw, str):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        text = payload.get("text", "")
        spans = keyword_ranges(text, rules, keywords)
        if not spans:
            continue
        styles = payload.get("styles", [])
        if not styles:
            continue
        base = styles[0]
        for start, end in spans:
            extra = json.loads(json.dumps(base))
            extra["range"] = [start, end]
            extra["bold"] = bold
            extra.setdefault("fill", {}).setdefault("content", {}).setdefault("solid", {})
            extra["fill"]["content"]["solid"]["color"] = list(color)
            if extra["fill"]["content"].get("render_type") is None:
                extra["fill"]["content"]["render_type"] = "solid"
            styles.append(extra)
        payload["styles"] = styles
        material["content"] = json.dumps(payload, ensure_ascii=False)
        touched += 1
    if touched:
        content_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return touched
