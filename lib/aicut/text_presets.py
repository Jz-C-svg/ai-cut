r"""文本预设复用 + 划词高亮全局开关（v3.1）。

与 edit2.py 的关系：
  - edit2.add_styled_text 已经支持 style="预设名/块名" 读取 config/text_styles.json；
  - 本模块在此基础上提供：
      1. apply_text_preset        —— 对**已有**文本片段事后套预设（而非新建时）
      2. set_global_keyword_highlight —— 草稿级「划词高亮」总开关（globalKeywordStyle）
      3. list_text_presets / save_text_preset —— 预设的列举与持久化
  - 划词高亮复用 text_segment.highlight_keywords() 的 N range 规范（is_rich_text + styles[]）。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .draft import Draft, new_id
from .paths import CONFIG_DIR

TEXT_STYLES_PATH = CONFIG_DIR / "text_styles.json"


# ---------- 预设读写 ----------


def _read_config() -> dict:
    if TEXT_STYLES_PATH.exists():
        try:
            return json.loads(TEXT_STYLES_PATH.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            pass
    return {"presets": {}}


def _write_config(cfg: dict) -> None:
    TEXT_STYLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEXT_STYLES_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def list_text_presets() -> list[dict]:
    """列举 config/text_styles.json 里所有预设。"""
    cfg = _read_config()
    out: list[dict] = []
    for name, blocks in (cfg.get("presets") or {}).items():
        out.append({
            "名称": name,
            "块": list(blocks.keys()) if isinstance(blocks, dict) else [],
            "字幕字号": (blocks.get("字幕") or {}).get("字号"),
            "字幕字体": (blocks.get("字幕") or {}).get("字体"),
        })
    return out


def save_text_preset(name: str, style_dict: dict, *, block: str = "字幕") -> dict:
    """把一个样式块写入 config/text_styles.json（同名同块覆盖）。"""
    cfg = _read_config()
    presets = cfg.setdefault("presets", {})
    preset = presets.setdefault(name, {})
    preset[block] = dict(style_dict)
    _write_config(cfg)
    return {"op": "save_text_preset", "名称": name, "块": block,
            "字段": list(style_dict.keys())}


# ---------- 颜色工具 ----------


def _rgb01(color) -> list[float]:
    """归一化成 [r,g,b] 0-1。支持 list/tuple/hex。"""
    if color is None:
        return [1.0, 1.0, 1.0]
    if isinstance(color, str):
        s = color.lstrip("#")
        if len(s) == 6:
            return [round(int(s[0:2], 16) / 255.0, 6),
                    round(int(s[2:4], 16) / 255.0, 6),
                    round(int(s[4:6], 16) / 255.0, 6)]
        return [1.0, 1.0, 1.0]
    return [round(float(color[0]), 6), round(float(color[1]), 6),
            round(float(color[2]), 6)]


# ---------- 对已有文本片段套预设 ----------


def apply_text_preset(draft: Draft, segment_id: str, preset_name: str, *,
                      block: str = "字幕", **overrides: Any) -> dict:
    """把预设样式应用到**已有**文本片段。

    优先级：预设值 < 显式 override（显式参数覆盖预设）。
    支持的字段：字体/字号/颜色/加粗/描边颜色/描边宽度/背景色/位置y。

    Args:
        draft: 草稿。
        segment_id: 文本片段 id（或前缀）。
        preset_name: 预设名，如「口播标准」。可带 /块 或 :块（如 "口播标准/封面"）。
        block: 块名（默认「字幕」）。若 preset_name 里已带 / 或 :，以此为准。
        **overrides: 显式覆盖，如 font=..., size=..., color=..., y=...。
    """
    # 解析预设名（支持 "名/块" 写法）
    for sep in ("/", ":"):
        if sep in preset_name:
            preset_name, block = preset_name.split(sep, 1)
            break

    cfg = _read_config()
    preset = (cfg.get("presets") or {}).get(preset_name.strip(), {}).get(block.strip(), {})
    if not preset:
        raise KeyError(f"预设 {preset_name!r}/{block!r} 不存在；可用：{list_text_presets()}")

    # 找到片段
    seg = None
    for s in draft.all_segments():
        if s.id == segment_id or s.id.startswith(segment_id):
            seg = s
            break
    if seg is None:
        raise KeyError(f"找不到片段 {segment_id}")

    mat_id = seg.raw.get("material_id", "")
    mat = draft.material(mat_id)
    if mat is None:
        raise KeyError(f"片段 {segment_id} 没有对应文本素材")

    # 解析 content JSON
    try:
        content = json.loads(mat.get("content") or "{}")
    except ValueError:
        content = {"styles": [], "text": ""}
    styles = content.setdefault("styles", [])
    if not styles:
        styles.append({"range": [0, len(content.get("text", ""))]})
    s0 = styles[0]

    changes: dict[str, Any] = {}

    # --- 字体 ---
    font_name = overrides.get("font") or preset.get("字体")
    if font_name:
        from .edit2 import _resolve_font  # noqa: PLC0415
        resolved = _resolve_font(font_name)
        if resolved and resolved[0]:
            s0["font"] = {"id": resolved[0], "path": "C:/%s.ttf" % resolved[1]}
            changes["字体"] = resolved[1]

    # --- 字号 ---
    size_val = overrides.get("size")
    if size_val is None and preset.get("字号") is not None:
        size_val = float(preset["字号"])
    if size_val is not None:
        s0["size"] = float(size_val)
        changes["字号"] = s0["size"]

    # --- 颜色 ---
    color_val = overrides.get("color")
    if color_val is None and preset.get("颜色") is not None:
        color_val = preset["颜色"]
    if color_val is not None:
        rgb = _rgb01(color_val)
        s0.setdefault("fill", {}).setdefault("content", {}).setdefault("solid", {})["color"] = rgb
        s0["fill"].setdefault("alpha", 1.0)
        s0["fill"]["content"].setdefault("render_type", "solid")
        s0["fill"]["content"]["solid"].setdefault("alpha", 1.0)
        changes["颜色"] = rgb

    # --- 加粗 ---
    bold_val = overrides.get("bold")
    if bold_val is None and preset.get("加粗") is not None:
        bold_val = bool(preset["加粗"])
    if bold_val is not None:
        s0["bold"] = bool(bold_val)
        changes["加粗"] = s0["bold"]

    # --- 描边 ---
    border_preset = preset.get("描边") or {}
    border_color = overrides.get("border_color")
    if border_color is None and border_preset.get("颜色") is not None:
        border_color = border_preset["颜色"]
    border_width = overrides.get("border_width")
    if border_width is None and border_preset.get("宽度") is not None:
        border_width = float(border_preset["宽度"])
    if border_color is not None or (border_width is not None and float(border_width) > 0):
        bw = 40.0 if border_width is None else float(border_width)
        width_us = round(bw / 100.0 * 0.2, 6)
        s0["strokes"] = [{
            "content": {"solid": {"alpha": 1.0, "color": _rgb01(border_color or [0, 0, 0])}},
            "width": width_us,
        }]
        mat["check_flag"] = int(mat.get("check_flag", 7)) | 8
        changes["描边"] = {"宽度": bw, "颜色": _rgb01(border_color or [0, 0, 0])}

    # --- 背景 ---
    bg_color = overrides.get("background_color")
    if bg_color is not None:
        rgb = _rgb01(bg_color)
        mat["background_style"] = 1
        mat["background_color"] = "#{:02X}{:02X}{:02X}".format(
            int(round(rgb[0] * 255)), int(round(rgb[1] * 255)), int(round(rgb[2] * 255)))
        mat["background_alpha"] = 1.0
        mat["check_flag"] = int(mat.get("check_flag", 7)) | 16
        changes["背景"] = mat["background_color"]

    # --- 位置 y ---
    y_val = overrides.get("y")
    if y_val is None and preset.get("位置") is not None:
        y_val = float(preset["位置"])
    if y_val is not None:
        seg.raw.setdefault("clip", {}).setdefault("transform", {})["y"] = float(y_val)
        changes["位置y"] = float(y_val)

    mat["content"] = json.dumps(content, ensure_ascii=False)
    draft._mat_index = None

    return {"op": "apply_text_preset", "segment": seg.id, "预设": preset_name,
            "块": block, "改动": changes}


# ---------- 划词高亮全局开关 ----------


def set_global_keyword_highlight(draft: Draft, enabled: bool,
                                 color: list[float] | None = None,
                                 *, keywords: list[str] | None = None) -> dict:
    """设置草稿级「划词高亮」全局默认。

    写入 draft_content.json 根级字段 `globalKeywordStyle`：
      - enabled=True 时，后续新增字幕自动对关键词应用高亮色；
      - color 为高亮 RGB [r,g,b] 0-1；
      - keywords 为全局关键词列表（如 ["数字", "关键词"]）。

    与 v3.0 的 highlight_keywords() 衔接：本开关只是草稿级标记，
    实际高亮仍由 add_highlighted_subtitle / highlight_keywords() 生成 N range。
    """
    gks: dict[str, Any] = {"enabled": bool(enabled)}
    if color is not None:
        gks["color"] = [float(c) for c in color]
    if keywords is not None:
        gks["keywords"] = list(keywords)

    draft.content["globalKeywordStyle"] = gks

    # 同时把颜色镜像到 materials.texts 里已有字幕的高亮（如果有高亮色预设）
    if enabled and color is not None:
        # 不批量改已有字幕——只是标记；已有字幕的高亮由 add_highlighted_subtitle 单独处理
        pass

    return {"op": "set_global_keyword_highlight", "enabled": bool(enabled),
            "color": gks.get("color"), "keywords": gks.get("keywords")}


def get_global_keyword_highlight(draft: Draft) -> dict:
    """读取草稿级划词高亮开关。"""
    return draft.content.get("globalKeywordStyle") or {"enabled": False}
