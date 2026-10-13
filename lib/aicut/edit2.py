r"""增量编辑原语（第二批）：切分、加字幕、加转场、加滤镜。

设计原则（"最精细"的落法）：
  1. **能复用就复用现有素材形状**：新字幕的样式直接以草稿里已有的字幕素材为模板，
     不自己拍脑袋拼 JSON——这样出来的东西和剪映里手工加的完全一致；
  2. **素材形状由草稿库的工厂类生成**（Filter / Transition），它们是验证过能被剪映读的；
  3. 每次操作只动必要字段，且都返回"改了什么"，供 diff 与验证；
  4. 所有时间对外用秒，内部微秒。
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

from .draft import Draft, Segment, new_id, to_us

JIANYING_LIB = Path(__file__).resolve().parent.parent / "JianYingDraft"


def _lib():
    """按需导入草稿库（它依赖较重的元数据，用到才加载）。"""
    import sys

    lib_parent = str(JIANYING_LIB.parent)
    if lib_parent not in sys.path:
        sys.path.insert(0, lib_parent)
    import JianYingDraft as jd  # noqa: PLC0415
    from JianYingDraft.metadata.filter_meta import FilterType  # noqa: PLC0415
    from JianYingDraft.metadata.transition_meta import TransitionType  # noqa: PLC0415

    return jd, FilterType, TransitionType


# ---------- 时间线手术 ----------


def split_at(draft: Draft, t: float, *, track_types: tuple[str, ...] = ("video", "audio", "text",
                                                                        "effect", "filter")) -> dict:
    """在 t 秒处切开所有跨越该点的片段（源时间连续、关键帧按时长裁）。"""
    cut = to_us(t)
    report: dict = {"op": "split_at", "t": round(t, 3), "cut": []}

    for track in draft.tracks:
        if track.type not in track_types:
            continue
        out: list[dict] = []
        for seg in track.segments:
            rng = seg.raw.get("target_timerange") or {}
            start = int(rng.get("start", 0))
            dur = int(rng.get("duration", 0))
            end = start + dur
            if start + 1000 < cut < end - 1000:  # 至少两边各留 1ms
                right = copy.deepcopy(seg.raw)
                right["id"] = new_id()
                left_dur = cut - start
                right_dur = end - cut
                speed = seg.speed or 1.0
                src = seg.raw.get("source_timerange")
                rng["duration"] = left_dur
                if src:
                    seg.raw["source_timerange"] = {
                        "start": int(src.get("start", 0)),
                        "duration": int(round(left_dur * speed)),
                    }
                right["target_timerange"] = {"start": cut, "duration": right_dur}
                if src:
                    right["source_timerange"] = {
                        "start": int(src.get("start", 0)) + int(round(left_dur * speed)),
                        "duration": int(round(right_dur * speed)),
                    }
                out.append(seg.raw)
                out.append(right)
                report["cut"].append({"track": track.type, "segment": seg.id,
                                      "left": round(left_dur / 1e6, 3),
                                      "right": round(right_dur / 1e6, 3)})
            else:
                out.append(seg.raw)
        track.raw["segments"] = out

    draft._tracks = None
    return report


def remove_segment(draft: Draft, seg_id: str, *, ripple: bool = True) -> dict:
    """删除一个片段；ripple=True 时把后面的内容前移。"""
    target = None
    for track in draft.tracks:
        for seg in track.segments:
            if seg.id == seg_id or seg.id.startswith(seg_id):
                target = (track, seg)
                break
        if target:
            break
    if not target:
        raise KeyError(f"找不到片段 {seg_id}")
    track, seg = target
    t0, t1 = seg.t, seg.end
    if ripple:
        from .edit import ripple_delete

        rep = ripple_delete(draft, t0, t1)
        rep.update({"op": "remove_segment", "id": seg.id, "ripple": True})
        return rep
    track.raw["segments"] = [s.raw for s in track.segments if s.id != seg.id]
    draft._tracks = None
    draft._mat_index = None
    return {"op": "remove_segment", "id": seg.id, "ripple": False, "removed": [t0, t1]}


def set_gain_db(seg: Segment, db: float) -> None:
    """按分贝设音量（-60dB ≈ 静音，0dB 原始）。"""
    ratio = 0.0 if db <= -60 else float(10 ** (db / 20.0))
    seg.raw["volume"] = round(ratio, 4)


# ---------- 字幕 ----------


def _text_material_template(draft: Draft) -> dict | None:
    """拿草稿里已有的字幕素材当样式模板（保证与手工加的完全一致）。"""
    for mat in draft.materials_of("texts"):
        try:
            content = json.loads(mat.get("content") or "{}")
        except ValueError:
            continue
        if content.get("text") and content.get("styles"):
            return mat
    return None


def _content_for(text: str, template: dict | None) -> str:
    if template:
        try:
            content = json.loads(template.get("content") or "{}")
        except ValueError:
            content = {}
        content["text"] = text
        for style in content.get("styles") or []:
            if isinstance(style.get("range"), list) and len(style["range"]) == 2:
                style["range"] = [0, len(text)]
        return json.dumps(content, ensure_ascii=False)
    return json.dumps({
        "styles": [{
            "fill": {"alpha": 1.0, "content": {"render_type": "solid",
                                               "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}}},
            "range": [0, len(text)], "size": 6.0, "bold": True, "italic": False,
            "underline": False,
            "strokes": [{"content": {"solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]}},
                         "width": 0.08}],
        }],
        "text": text,
    }, ensure_ascii=False)


def _text_segment_template(draft: Draft) -> dict | None:
    for seg in draft.text_segments:
        if seg.track_type == "text":
            return seg.raw
    return None


def _subtitle_track(draft: Draft):
    """字幕轨 = 文本轨里片段最多的那条；没有就新建一条。"""
    text_tracks = [t for t in draft.tracks if t.type == "text"]
    if text_tracks:
        return max(text_tracks, key=lambda t: len(t.segments))
    raw = {"id": new_id(), "type": "text", "flag": 0, "attribute": 0,
           "segments": [], "is_default_name": False}
    draft.content.setdefault("tracks", []).append(raw)
    draft._tracks = None
    return draft.tracks[[t.type for t in draft.tracks].index("text")]


def add_subtitle(draft: Draft, text: str, t0: float, t1: float, *, y: float | None = None,
                 line_max_width: float | None = None, overlap: str = "new_track") -> dict:
    """在时间线 [t0,t1) 加一条字幕。样式优先沿用草稿里已有的字幕素材。"""
    if t1 <= t0:
        raise ValueError("t1 必须大于 t0")
    text = str(text).strip()
    if not text:
        raise ValueError("字幕内容为空")

    template_mat = _text_material_template(draft)
    material = copy.deepcopy(template_mat) if template_mat else {
        "typesetting": 0, "alignment": 1, "letter_spacing": 0.0, "line_spacing": 0.02,
        "line_feed": 1, "line_max_width": 0.82, "force_apply_line_max_width": False,
        "check_flag": 15, "type": "subtitle", "global_alpha": 1.0,
    }
    material["id"] = new_id()
    material["content"] = _content_for(text, template_mat)
    if line_max_width is not None:
        material["line_max_width"] = float(line_max_width)
    draft.content.setdefault("materials", {}).setdefault("texts", []).append(material)
    draft._mat_index = None

    template_seg = _text_segment_template(draft)
    seg = copy.deepcopy(template_seg) if template_seg else {
        "enable_adjust": True, "visible": True, "common_keyframes": [], "keyframe_refs": [],
        "source_timerange": None, "speed": 1.0, "volume": 1.0, "extra_material_refs": [],
        "is_tone_modify": False, "uniform_scale": {"on": True, "value": 1.0},
        "clip": {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
                 "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": -0.78}},
    }
    seg["id"] = new_id()
    seg["material_id"] = material["id"]
    seg["target_timerange"] = {"start": to_us(t0), "duration": to_us(t1 - t0)}
    seg["render_index"] = 15000
    seg["common_keyframes"] = []
    seg["keyframe_refs"] = []
    if not template_seg:
        seg["extra_material_refs"] = []
    if y is not None:
        seg.setdefault("clip", {}).setdefault("transform", {})["y"] = float(y)

    track = _subtitle_track(draft)
    overlaps = [s.id for s in track.segments
                if s.t < t1 - 0.001 and s.end > t0 + 0.001]
    if overlaps and overlap == "new_track":
        # 不压掉原字幕：落到一条新的文本轨上
        draft.content.setdefault("tracks", []).append({
            "id": new_id(), "type": "text", "flag": 0, "attribute": 0,
            "segments": [], "is_default_name": False})
        draft._tracks = None
        track = [x for x in draft.tracks if x.type == "text"][-1]
    track.raw["segments"].append(seg)
    track.raw["segments"].sort(key=lambda s: s.get("target_timerange", {}).get("start", 0))
    draft._tracks = None
    return {"op": "add_subtitle", "text": text, "range": [round(t0, 3), round(t1, 3)],
            "material": material["id"], "segment": seg["id"],
            "用模板样式": bool(template_mat), "重叠": overlaps,
            "落到新轨": bool(overlaps and overlap == "new_track"), "轨道": track.id}


def _find_member(enum_cls, name: str):
    """按成员名或显示名（中文）找枚举成员，找不到返回 None。"""
    if name in enum_cls.__members__:
        return enum_cls.__members__[name]
    target = str(name).strip()
    for member in enum_cls.__members__.values():
        meta = getattr(member, "value", None)
        display = getattr(meta, "name", "")
        if display == target:
            return member
    for member in enum_cls.__members__.values():
        meta = getattr(member, "value", None)
        display = getattr(meta, "name", "")
        if target and (target in display or display in target):
            return member
    return None


def list_transitions(keyword: str = "", limit: int = 40) -> list[dict]:
    _, _, TransitionType = _lib()
    out = []
    for member in TransitionType.__members__.values():
        meta = member.value
        name = getattr(meta, "name", member.name)
        if keyword and keyword not in str(name):
            continue
        out.append({"名称": name, "默认时长秒": round(getattr(meta, "default_duration", 500000) / 1e6, 2),
                    "会员": bool(getattr(meta, "is_vip", False))})
        if len(out) >= limit:
            break
    return out


def list_filters(keyword: str = "", limit: int = 40) -> list[dict]:
    _, FilterType, _ = _lib()
    out = []
    for member in FilterType.__members__.values():
        meta = member.value
        name = getattr(meta, "name", member.name)
        if keyword and keyword not in str(name):
            continue
        out.append({"名称": name, "会员": bool(getattr(meta, "is_vip", False))})
        if len(out) >= limit:
            break
    return out


def _segment_by_id(draft: Draft, seg_id: str) -> Segment:
    for seg in draft.all_segments():
        if seg.id == seg_id or seg.id.startswith(seg_id):
            return seg
    raise KeyError(f"找不到片段 {seg_id}")


def add_transition(draft: Draft, seg_id: str, name: str, *, seconds: float = 0.5) -> dict:
    """给片段加转场（素材形状由草稿库的 Transition 工厂生成）。"""
    jd, _, TransitionType = _lib()
    member = _find_member(TransitionType, name)
    if member is None:
        raise KeyError(f"没有这个转场：{name}（可用 {len(TransitionType.__members__)} 种）")
    seg = _segment_by_id(draft, seg_id)
    duration_us = int(round(seconds * 1e6)) or int(getattr(member.value, "default_duration", 500000))
    inst = jd.video_segment.Transition(member, duration_us)
    material = inst.export_json()
    draft.content.setdefault("materials", {}).setdefault("transitions", []).append(material)
    draft._mat_index = None
    refs = seg.raw.setdefault("extra_material_refs", [])
    if material["id"] not in refs:
        refs.append(material["id"])
    return {"op": "add_transition", "segment": seg.id, "名称": material["name"],
            "时长秒": round(duration_us / 1e6, 3), "material": material["id"]}


def add_filter(draft: Draft, seg_id: str, name: str, *, intensity: float = 50.0) -> dict:
    """给片段加滤镜（强度 0-100）。"""
    jd, FilterType, _ = _lib()
    member = _find_member(FilterType, name)
    if member is None:
        raise KeyError(f"没有这个滤镜：{name}")
    seg = _segment_by_id(draft, seg_id)
    inst = jd.video_segment.Filter(member.value, float(intensity) / 100.0)
    material = inst.export_json()
    draft.content.setdefault("materials", {}).setdefault("filters", []).append(material)
    draft._mat_index = None
    refs = seg.raw.setdefault("extra_material_refs", [])
    if material["id"] not in refs:
        refs.append(material["id"])
    return {"op": "add_filter", "segment": seg.id, "名称": material["name"],
            "强度": round(float(intensity), 1), "material": material["id"]}


def describe_ops() -> list[str]:
    return ["split_at(draft, t)", "remove_segment(draft, seg_id, ripple=True)",
            "add_subtitle(draft, text, t0, t1, y=None, line_max_width=None)",
            "add_styled_text(draft, text, t0, t1, style=None, font=None, color=None, size=None,",
            "                bold=None, border_color=None, border_width=None, background_color=None, y=None)",
            "add_text_animation(draft, segment_id, intro=None, outro=None, loop=None,",
            "                    intro_duration=0.5, outro_duration=0.5)",
            "add_stacked_subtitles(draft, subtitle_list, max_stack_depth=2, stagger_us=100000, y_center=0.8)",
            "align_and_validate_subtitles(draft, source_events, max_shift=0.3)",
            "add_transition(draft, seg_id, name, seconds=0.5)",
            "add_filter(draft, seg_id, name, intensity=50)",
            "set_gain_db(seg, db)",
            "add_keyframe_animation(draft, segment_id, property_name, keyframes)"
            "  # {t,value,control_in?,control_out?}"]


# ---------- 花字 / 文本样式预设 ----------


def _font_type_cls():
    from JianYingDraft.metadata import FontType  # noqa: PLC0415

    return FontType


def _resolve_font(name: str):
    """按字体显示名（如「抖音美好体」）解析出 (resource_id, 显示名)；找不到返回 None。"""
    FontType = _font_type_cls()
    key = str(name).strip()
    # 先按枚举成员名 / 显示名精确匹配，再做包含匹配
    for member in FontType.__members__.values():
        meta = member.value
        if member.name == key or getattr(meta, "name", "") == key:
            return getattr(meta, "resource_id", None), getattr(meta, "name", member.name)
    for member in FontType.__members__.values():
        meta = getattr(member, "value", None)
        disp = getattr(meta, "name", "") if meta else ""
        if key and (key in disp or disp in key):
            return getattr(meta, "resource_id", None), disp
    return None


def _load_text_styles() -> dict:
    """读取 config/text_styles.json 预设。"""
    try:
        from .paths import CONFIG_DIR  # noqa: PLC0415

        p = CONFIG_DIR / "text_styles.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    return {}


def _preset_block(style: str) -> dict:
    """把 style 参数解析成预设块。

    支持写法：
      "口播标准"        -> 预设「口播标准」的「字幕」块
      "口播标准/封面"   -> 预设「口播标准」的「封面」块
      "口播标准:封面"   -> 同上
    找不到时返回 {}。
    """
    if not style:
        return {}
    presets = _load_text_styles().get("presets", {})
    preset_name = str(style)
    block_name = "字幕"
    for sep in ("/", ":"):
        if sep in preset_name:
            preset_name, block_name = preset_name.split(sep, 1)
            break
    preset = presets.get(preset_name.strip(), {})
    return preset.get(block_name.strip(), {}) or {}


def _rgb(color) -> list[float]:
    """把颜色归一化成 [r,g,b] 0-1。支持 list/tuple 或 '#RRGGBB'。"""
    if color is None:
        return [1.0, 1.0, 1.0]
    if isinstance(color, str):
        s = color.lstrip("#")
        if len(s) == 6:
            return [round(int(s[0:2], 16) / 255.0, 6),
                    round(int(s[2:4], 16) / 255.0, 6),
                    round(int(s[4:6], 16) / 255.0, 6)]
        return [1.0, 1.0, 1.0]
    r, g, b = color[0], color[1], color[2]
    return [round(float(r), 6), round(float(g), 6), round(float(b), 6)]


def _to_hex(color) -> str:
    """把 RGB 0-1 或 '#RRGGBB' 归一化成 '#RRGGBB'。"""
    if isinstance(color, str):
        return color if color.startswith("#") else "#" + color
    rgb = _rgb(color)
    return "#{:02X}{:02X}{:02X}".format(int(round(rgb[0] * 255)),
                                       int(round(rgb[1] * 255)),
                                       int(round(rgb[2] * 255)))


def add_styled_text(draft: Draft, text: str, t0: float, t1: float, *,
                    style: str | None = None,
                    font: str | None = None,
                    color=None,
                    size: float | None = None,
                    bold: bool | None = None,
                    border_color=None,
                    border_width: float | None = None,
                    background_color=None,
                    y: float | None = None) -> dict:
    """在 [t0,t1) 加一条**自定义样式**文本（花字）。

    与 add_subtitle 的区别：字体/颜色/字号/加粗/描边/背景/位置全部可自定义，
    样式可来自 config/text_styles.json 的预设（style="口播标准"）。
    产出的 content JSON 与剪映原生格式逐字段对齐（styles[0].fill/strokes/font）。
    """
    if t1 <= t0:
        raise ValueError("t1 必须大于 t0")
    text = str(text).strip()
    if not text:
        raise ValueError("文本内容为空")

    # 1) 合并参数：预设 < 显式参数（显式覆盖预设）
    preset = _preset_block(style)
    if font is None and preset.get("字体"):
        font = preset["字体"]
    if size is None and preset.get("字号") is not None:
        size = float(preset["字号"])
    if color is None and preset.get("颜色") is not None:
        color = preset["颜色"]
    if bold is None and preset.get("加粗") is not None:
        bold = bool(preset["加粗"])
    presets_border = preset.get("描边") or {}
    if border_color is None and presets_border.get("颜色") is not None:
        border_color = presets_border["颜色"]
    if border_width is None and presets_border.get("宽度") is not None:
        border_width = float(presets_border["宽度"])
    if y is None and preset.get("位置") is not None:
        y = float(preset["位置"])

    size = float(size) if size is not None else 6.0
    bold = bool(bold) if bold is not None else True
    text_color = _rgb(color)

    # 2) 组装 content.styles[0]（与 TextSegment.export_material 一致）
    style0: dict = {
        "fill": {"alpha": 1.0, "content": {"render_type": "solid",
                                           "solid": {"alpha": 1.0, "color": text_color}}},
        "range": [0, len(text)],
        "size": size,
        "bold": bold,
        "italic": False,
        "underline": False,
        "strokes": [],
    }
    font_resolved = None
    if font:
        font_resolved = _resolve_font(font)
        if font_resolved and font_resolved[0]:
            style0["font"] = {"id": font_resolved[0], "path": "C:/%s.ttf" % font_resolved[1]}
        else:
            # 字体找不到也不报错：退回无字体（剪映用默认字体）
            font_resolved = None

    check_flag = 7  # base
    # 3) 描边
    if border_color is not None or (border_width is not None and border_width > 0):
        bw = 0.08 if border_width is None else float(border_width)
        # 与 TextBorder 一致：剪映里 0-100 -> 实际 width = /100*0.2
        width_us = round(bw / 100.0 * 0.2, 6)
        style0["strokes"] = [{
            "content": {"solid": {"alpha": 1.0, "color": _rgb(border_color)}},
            "width": width_us,
        }]
        check_flag |= 8

    content_json = {"styles": [style0], "text": text}

    # 4) 素材级字段
    material: dict = {
        "id": new_id(),
        "content": json.dumps(content_json, ensure_ascii=False),
        "typesetting": 0,
        "alignment": 1,
        "letter_spacing": 0.0,
        "line_spacing": 0.02,
        "line_feed": 1,
        "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": check_flag,
        "type": "subtitle",
        "global_alpha": 1.0,
    }

    # 5) 背景（TextBackground）
    if background_color is not None:
        material.update({
            "background_style": 1,
            "background_color": _to_hex(background_color),
            "background_alpha": 1.0,
            "background_round_radius": 0.0,
            "background_height": 0.14,
            "background_width": 0.14,
            "background_horizontal_offset": 0.0,
            "background_vertical_offset": 0.0,
        })
        check_flag |= 16
        material["check_flag"] = check_flag

    draft.content.setdefault("materials", {}).setdefault("texts", []).append(material)
    draft._mat_index = None

    # 6) 片段
    template_seg = _text_segment_template(draft)
    seg = copy.deepcopy(template_seg) if template_seg else {
        "enable_adjust": True, "visible": True, "common_keyframes": [], "keyframe_refs": [],
        "source_timerange": None, "speed": 1.0, "volume": 1.0, "extra_material_refs": [],
        "is_tone_modify": False, "uniform_scale": {"on": True, "value": 1.0},
        "clip": {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
                 "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": -0.78}},
    }
    seg["id"] = new_id()
    seg["material_id"] = material["id"]
    seg["target_timerange"] = {"start": to_us(t0), "duration": to_us(t1 - t0)}
    seg["render_index"] = 15000
    seg["common_keyframes"] = []
    seg["keyframe_refs"] = []
    seg["extra_material_refs"] = []
    if y is not None:
        seg.setdefault("clip", {}).setdefault("transform", {})["y"] = float(y)

    track = _subtitle_track(draft)
    overlaps = [s.id for s in track.segments
                if s.t < t1 - 0.001 and s.end > t0 + 0.001]
    if overlaps:
        # 与既有文本重叠：落到一条新的文本轨，不压掉原有内容
        draft.content.setdefault("tracks", []).append({
            "id": new_id(), "type": "text", "flag": 0, "attribute": 0,
            "segments": [], "is_default_name": False})
        draft._tracks = None
        track = [x for x in draft.tracks if x.type == "text"][-1]
    track.raw["segments"].append(seg)
    track.raw["segments"].sort(key=lambda s: s.get("target_timerange", {}).get("start", 0))
    draft._tracks = None

    return {
        "op": "add_styled_text", "text": text, "range": [round(t0, 3), round(t1, 3)],
        "material": material["id"], "segment": seg["id"],
        "字体": font_resolved[1] if font_resolved else (font or "默认"),
        "字号": size, "颜色": text_color, "加粗": bold,
        "描边": bool(style0["strokes"]), "背景": background_color is not None,
        "预设": style, "轨道": track.id, "重叠": overlaps,
        "落到新轨": bool(overlaps),
    }


# ---------- 文本动画（入场 / 出场 / 循环） ----------


def _text_anim_enum(kind: str):
    from JianYingDraft.metadata import TextIntro, TextOutro, TextLoopAnim  # noqa: PLC0415

    return {"intro": TextIntro, "outro": TextOutro, "loop": TextLoopAnim}[kind]


def _find_text_anim(kind: str, name: str):
    """按显示名找文本动画枚举成员。"""
    cls = _text_anim_enum(kind)
    target = str(name).strip()
    for member in cls.__members__.values():
        if member.name == target or getattr(member.value, "title", "") == target:
            return member
    for member in cls.__members__.values():
        title = getattr(member.value, "title", "")
        if target and (target in title or title in target):
            return member
    return None


def _build_anim_material(anim_id: str, items: list[dict]) -> dict:
    return {
        "id": anim_id,
        "type": "sticker_animation",
        "multi_language_current": "none",
        "animations": items,
    }


def _anim_item(kind_type: str, member, start: int, duration: int) -> dict:
    meta = member.value
    return {
        "anim_adjust_params": None,
        "platform": "all",
        "panel": "",
        "material_type": "sticker",
        "name": getattr(meta, "title", member.name),
        "id": getattr(meta, "effect_id", ""),
        "type": kind_type,
        "resource_id": getattr(meta, "resource_id", ""),
        "start": int(start),
        "duration": int(duration),
    }


def add_text_animation(draft: Draft, segment_id: str, *,
                       intro: str | None = None, outro: str | None = None,
                       loop: str | None = None,
                       intro_duration: float = 0.5,
                       outro_duration: float = 0.5) -> dict:
    """给文本片段加入场/出场/循环动画，写入 materials.material_animations。

    时间安排：
      入场 [0, intro_duration)；出场 [seg_dur-outro_duration, seg_dur)；
      循环填满二者之间的剩余区间（若同时指定了循环）。
    """
    seg = _segment_by_id(draft, segment_id)
    seg_dur_us = int(seg.raw.get("target_timerange", {}).get("duration", 0))
    if seg_dur_us <= 0:
        raise ValueError("该文本片段时长为 0，无法加动画")

    items: list[dict] = []
    used: list[str] = []

    # 入场
    intro_member = _find_text_anim("intro", intro) if intro else None
    if intro and intro_member is None:
        raise KeyError(f"没有这个入场动画：{intro}")
    intro_dur_us = to_us(intro_duration) if intro else 0
    if intro_member:
        intro_dur_us = min(intro_dur_us or intro_member.value.duration, seg_dur_us)
        items.append(_anim_item("in", intro_member, 0, intro_dur_us))
        used.append(f"入场·{intro_member.value.title}")

    # 出场
    outro_member = _find_text_anim("outro", outro) if outro else None
    if outro and outro_member is None:
        raise KeyError(f"没有这个出场动画：{outro}")
    outro_dur_us = to_us(outro_duration) if outro else 0
    outro_start = 0
    if outro_member:
        outro_dur_us = min(outro_dur_us or outro_member.value.duration, seg_dur_us - intro_dur_us)
        outro_dur_us = max(outro_dur_us, 0)
        outro_start = seg_dur_us - outro_dur_us
        items.append(_anim_item("out", outro_member, outro_start, outro_dur_us))
        used.append(f"出场·{outro_member.value.title}")

    # 循环（填满剩余区间）
    loop_member = _find_text_anim("loop", loop) if loop else None
    if loop and loop_member is None:
        raise KeyError(f"没有这个循环动画：{loop}")
    if loop_member:
        loop_start = intro_dur_us
        loop_dur = seg_dur_us - loop_start - outro_dur_us
        if loop_dur > 0:
            items.append(_anim_item("loop", loop_member, loop_start, loop_dur))
            used.append(f"循环·{loop_member.value.title}")

    if not items:
        raise ValueError("至少要指定 intro/outro/loop 之一")

    # 找该片段已有的动画素材：extra_material_refs 里指向 material_animations 的那条
    mats = draft.content.setdefault("materials", {}).setdefault("material_animations", [])
    refs = seg.raw.setdefault("extra_material_refs", [])
    existing_id = None
    for ref in list(refs):
        for m in mats:
            if m.get("id") == ref and m.get("type") == "sticker_animation":
                existing_id = ref
                break
        if existing_id:
            break

    if existing_id:
        material = next(m for m in mats if m.get("id") == existing_id)
        material["animations"] = items
        anim_id = existing_id
    else:
        anim_id = new_id()
        mats.append(_build_anim_material(anim_id, items))
        refs.append(anim_id)
    draft._mat_index = None

    return {
        "op": "add_text_animation", "segment": seg.id,
        "时长us": seg_dur_us, "动画": used,
        "入场us": intro_dur_us, "出场us": outro_dur_us,
        "material_animation": anim_id,
    }


# ---------- v3.3 P0-1：关键帧动画（scale/position/rotation/opacity，贝塞尔控制点） ----------

# property_name 友好别名 -> KeyframeProperty 成员名
_KF_PROPERTY_ALIAS = {
    "scale": "uniform_scale",
    "uniform_scale": "uniform_scale",
    "position": "position_x",
    "position_x": "position_x",
    "position_y": "position_y",
    "rotation": "rotation",
    "opacity": "alpha",
    "alpha": "alpha",
    "volume": "volume",
}


def _resolve_kf_property(property_name: str):
    """把友好属性名解析成 KeyframeProperty 成员。"""
    from JianYingDraft.keyframe import KeyframeProperty  # noqa: PLC0415

    alias = _KF_PROPERTY_ALIAS.get(str(property_name).strip().lower(),
                                   str(property_name).strip())
    member = getattr(KeyframeProperty, alias, None)
    if member is None:
        raise KeyError(f"不支持的关键帧属性：{property_name}"
                       f"（可用 scale/position_x/position_y/position/rotation/opacity/volume）")
    return member


def add_keyframe_animation(draft: Draft, segment_id: str, property_name: str,
                           keyframes: list[dict]) -> dict:
    r"""给片段加关键帧动画（写入片段 raw.common_keyframes）。

    Args:
        draft: 草稿。
        segment_id: 目标片段 id（支持前缀匹配；视频/文本/图片段均可）。
        property_name: ``scale`` / ``position_x`` / ``position_y`` / ``position``(=position_x) /
            ``rotation`` / ``opacity``(=alpha) / ``volume``。
        keyframes: 关键帧列表，每项 ``{t, value, control_in?, control_out?}``；
            ``t`` 单位秒、**相对片段起点**（剪映内部语义）；``value`` 是该时刻属性值；
            ``control_in``/``control_out`` 可选，传 ``{"x":..,"y":..}`` 即开启贝塞尔缓动
            （curveType=FreeCurveInOut），不传则线性 Line。

    Returns:
        ``{"op","segment","property","keyframe_count","curve_types",...}``
    """
    from JianYingDraft.keyframe import Keyframe, KeyframeList  # noqa: PLC0415

    seg = _segment_by_id(draft, segment_id)
    member = _resolve_kf_property(property_name)

    kf_list = KeyframeList(member)
    curve_types: list[str] = []
    for item in keyframes:
        t = float(item["t"])
        value = float(item["value"])
        kf = Keyframe(to_us(t), value)
        ci, co = item.get("control_in"), item.get("control_out")
        if ci is not None or co is not None:
            kf.set_bezier(ci, co)
        kf_list.keyframes.append(kf)
        curve_types.append(kf._curve_type)  # noqa: SLF001
    if not kf_list.keyframes:
        raise ValueError("keyframes 不能为空")

    # 挂到片段 raw.common_keyframes（与库 Segment.export_json 同构）
    groups = seg.raw.setdefault("common_keyframes", [])
    # 同属性已有列表则替换其 keyframe_list（幂等：重复调用不叠加）
    replaced = False
    for g in groups:
        if g.get("property_type") == member.value:
            g["keyframe_list"] = [k.export_json() for k in kf_list.keyframes]
            replaced = True
            break
    if not replaced:
        groups.append(kf_list.export_json())

    return {
        "op": "add_keyframe_animation", "segment": seg.id,
        "property": member.value, "property_name": property_name,
        "keyframe_count": len(kf_list.keyframes),
        "replaced_existing": replaced,
        "curve_types": curve_types,
        "keyframes": [{
            "t": round(float(k.time_offset) / 1e6, 4),
            "value": k.values[0],
            "curveType": k.export_json()["curveType"],
            "left_control": k.export_json()["left_control"],
            "right_control": k.export_json()["right_control"],
        } for k in kf_list.keyframes],
    }


# ---------- TOP3 引擎扩展：文本阴影 / 关键词高亮 / 画中画混合模式 / 视频淡入淡出 ----------


def _hex_from_rgb01(rgb01) -> str:
    """RGB 0-1 -> #RRGGBB"""
    return "#{:02X}{:02X}{:02X}".format(
        int(round(min(1.0, max(0.0, rgb01[0])) * 255)),
        int(round(min(1.0, max(0.0, rgb01[1])) * 255)),
        int(round(min(1.0, max(0.0, rgb01[2])) * 255)),
    )


def add_shadow(draft: Draft, segment_id: str, *,
               color=(0.0, 0.0, 0.0), alpha: float = 0.9,
               offset_x: float = 0.3, offset_y: float = 0.3,
               blur: float = 0.45) -> dict:
    """给文本片段加阴影（TOP3 引擎扩展）。

    双写：外层镜像字段（has_shadow/shadow_*）+ content JSON 的 styles[0].shadows。
    """
    from JianYingDraft.text_segment import TextShadow  # noqa: PLC0415

    seg = _segment_by_id(draft, segment_id)
    mat_id = seg.raw.get("material_id")
    mat = draft.material(mat_id)
    if mat is None:
        raise KeyError(f"片段 {segment_id} 没有对应的文本素材")

    sh = TextShadow(color=tuple(color), alpha=alpha,
                   offset_x=offset_x, offset_y=offset_y, blur_radius=blur)
    # 1) 外层镜像字段
    mat.update(sh.export_outer_json())
    # check_flag +32
    mat["check_flag"] = int(mat.get("check_flag", 7)) | 32

    # 2) content JSON styles[0].shadows
    try:
        content = json.loads(mat.get("content") or "{}")
    except ValueError:
        content = {"styles": [], "text": ""}
    if not content.get("styles"):
        raise ValueError("文本素材 content.styles 为空，无法加阴影")
    content["styles"][0]["shadows"] = [sh.export_style_json()]
    mat["content"] = json.dumps(content, ensure_ascii=False)

    draft._mat_index = None
    return {
        "op": "add_shadow", "segment": seg.id, "material": mat_id,
        "color": list(color), "alpha": alpha,
        "offset": [offset_x, offset_y], "blur": blur,
    }


def add_highlighted_subtitle(draft: Draft, text: str, t0: float, t1: float, *,
                            keywords: list[str],
                            highlight_color=(1.0, 0.85, 0.2),
                            base_color=(1.0, 1.0, 1.0),
                            size: float = 6.0,
                            bold: bool = True,
                            y: float | None = None) -> dict:
    """加一条带关键词高亮的字幕（TOP3 引擎扩展）。

    按 David fork §4.3 编码：N 个连续非重叠 range，每个 range 一个完整样式块，
    content 根级 is_rich_text=True。
    """
    from JianYingDraft.text_segment import highlight_keywords  # noqa: PLC0415

    if t1 <= t0:
        raise ValueError("t1 必须大于 t0")
    text = str(text).strip()
    if not text:
        raise ValueError("文本内容为空")

    styles = highlight_keywords(text, list(keywords or []),
                               base_color=tuple(base_color),
                               highlight_color=tuple(highlight_color),
                               size=size, bold=bold)
    content_json = {"styles": styles, "text": text, "is_rich_text": True}

    material = {
        "id": new_id(),
        "content": json.dumps(content_json, ensure_ascii=False),
        "typesetting": 0,
        "alignment": 1,
        "letter_spacing": 0.0,
        "line_spacing": 0.02,
        "line_feed": 1,
        "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": 7,
        "type": "subtitle",
        "global_alpha": 1.0,
    }
    draft.content.setdefault("materials", {}).setdefault("texts", []).append(material)
    draft._mat_index = None

    template_seg = _text_segment_template(draft)
    seg = copy.deepcopy(template_seg) if template_seg else {
        "enable_adjust": True, "visible": True, "common_keyframes": [], "keyframe_refs": [],
        "source_timerange": None, "speed": 1.0, "volume": 1.0, "extra_material_refs": [],
        "is_tone_modify": False, "uniform_scale": {"on": True, "value": 1.0},
        "clip": {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
                 "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": -0.78}},
    }
    seg["id"] = new_id()
    seg["material_id"] = material["id"]
    seg["target_timerange"] = {"start": to_us(t0), "duration": to_us(t1 - t0)}
    seg["render_index"] = 15000
    seg["common_keyframes"] = []
    seg["keyframe_refs"] = []
    seg["extra_material_refs"] = []
    if y is not None:
        seg.setdefault("clip", {}).setdefault("transform", {})["y"] = float(y)

    track = _subtitle_track(draft)
    track.raw["segments"].append(seg)
    track.raw["segments"].sort(key=lambda s: s.get("target_timerange", {}).get("start", 0))
    draft._tracks = None

    return {
        "op": "add_highlighted_subtitle", "text": text,
        "range": [round(t0, 3), round(t1, 3)],
        "material": material["id"], "segment": seg["id"],
        "关键词": list(keywords or []), "styles": len(styles),
        "highlight_color": list(highlight_color),
        "轨道": track.id,
    }


def set_mix_mode(draft: Draft, segment_id: str, mode_name: str) -> dict:
    """给视频片段设置画中画混合模式（TOP3 引擎扩展）。

    mode_name 可以是枚举成员名（如 "滤色"）或显示名。
    """
    from JianYingDraft.metadata.mix_mode_meta import MixModeType  # noqa: PLC0415
    from JianYingDraft.video_segment import MixMode  # noqa: PLC0415

    member = _find_member(MixModeType, mode_name)
    if member is None:
        raise KeyError(f"没有这个混合模式：{mode_name}（可用 {len(MixModeType.__members__)} 种）")

    seg = _segment_by_id(draft, segment_id)
    inst = MixMode(member.value)
    material = inst.export_json()
    draft.content.setdefault("materials", {}).setdefault("effects", []).append(material)
    draft._mat_index = None
    refs = seg.raw.setdefault("extra_material_refs", [])
    if material["id"] not in refs:
        refs.append(material["id"])
    return {
        "op": "set_mix_mode", "segment": seg.id,
        "模式": material["name"], "effect_id": material["effect_id"],
        "material": material["id"],
    }


def add_video_fade(draft: Draft, segment_id: str,
                   in_seconds: float = 0.0, out_seconds: float = 0.0) -> dict:
    """给视频片段加画面淡入/淡出（TOP3 引擎扩展）。

    实现：渐显（入场动画）+ 渐隐（出场动画）。**不用 alpha 关键帧**（渲染时被忽略）。
    """
    from JianYingDraft.metadata import IntroType, OutroType  # noqa: PLC0415

    seg = _segment_by_id(draft, segment_id)
    seg_dur_us = int(seg.raw.get("target_timerange", {}).get("duration", 0))
    if seg_dur_us <= 0:
        raise ValueError("该视频片段时长为 0，无法加淡入淡出")

    in_us = int(round(in_seconds * 1e6)) if in_seconds else 0
    out_us = int(round(out_seconds * 1e6)) if out_seconds else 0
    in_us = min(in_us, seg_dur_us)
    out_us = min(out_us, seg_dur_us - in_us)
    if in_us <= 0 and out_us <= 0:
        raise ValueError("in_seconds 和 out_seconds 至少一个 > 0")

    items: list[dict] = []
    used: list[str] = []
    if in_us > 0:
        meta = IntroType.渐显.value
        items.append({
            "anim_adjust_params": None, "platform": "all", "panel": "video",
            "material_type": "video",
            "name": getattr(meta, "title", "渐显"),
            "id": meta.effect_id, "type": "in",
            "resource_id": meta.resource_id,
            "start": 0, "duration": in_us,
        })
        used.append(f"入场·渐显 {in_us/1e6:.2f}s")
    if out_us > 0:
        meta = OutroType.渐隐.value
        items.append({
            "anim_adjust_params": None, "platform": "all", "panel": "video",
            "material_type": "video",
            "name": getattr(meta, "title", "渐隐"),
            "id": meta.effect_id, "type": "out",
            "resource_id": meta.resource_id,
            "start": seg_dur_us - out_us, "duration": out_us,
        })
        used.append(f"出场·渐隐 {out_us/1e6:.2f}s")

    mats = draft.content.setdefault("materials", {}).setdefault("material_animations", [])
    refs = seg.raw.setdefault("extra_material_refs", [])
    existing_id = None
    for ref in list(refs):
        for m in mats:
            if m.get("id") == ref and m.get("type") == "sticker_animation":
                existing_id = ref
                break
        if existing_id:
            break

    if existing_id:
        material = next(m for m in mats if m.get("id") == existing_id)
        # 保留原有动画，合并 in/out（去重 type）
        old = list(material.get("animations") or [])
        merged: list[dict] = []
        seen_types: set[str] = set()
        for it in items:
            merged.append(it)
            seen_types.add(it["type"])
        for it in old:
            if it.get("type") not in seen_types:
                merged.append(it)
        material["animations"] = merged
        anim_id = existing_id
    else:
        anim_id = new_id()
        mats.append({
            "id": anim_id, "type": "sticker_animation",
            "multi_language_current": "none", "animations": items,
        })
        refs.append(anim_id)
    draft._mat_index = None

    return {
        "op": "add_video_fade", "segment": seg.id,
        "时长us": seg_dur_us, "动画": used,
        "淡入us": in_us, "淡出us": out_us,
        "material_animation": anim_id,
    }


def add_per_char_text(draft: Draft, text: str, t0: float, t1: float, *,
                      colors: list, sizes: list | None = None,
                      bold: bool = True, size: float = 6.0,
                      y: float | None = None) -> dict:
    """加一条逐字级颜色/字号不同的文本（TOP3 引擎扩展·words[]）。

    Args:
        text: 原文。
        colors: 每字颜色 [r,g,b] 0-1，长度 == len(text)。
        sizes: 每字字号（可选）；None 表示全用 size。
        bold: 是否加粗。
        size: 默认字号（无 sizes 时统一用）。
        y: 竖直位置。
    """
    from JianYingDraft.text_segment import build_per_char_words  # noqa: PLC0415

    if t1 <= t0:
        raise ValueError("t1 必须大于 t0")
    text = str(text).strip()
    if not text:
        raise ValueError("文本内容为空")
    if len(colors) != len(text):
        raise ValueError(f"colors 长度 {len(colors)} != 文本长度 {len(text)}")

    words = build_per_char_words(text, list(colors), sizes)
    c0 = list(colors[0])
    content_json = {
        "styles": [{
            "range": [0, len(text)],
            "size": size, "bold": bool(bold),
            "italic": False, "underline": False,
            "fill": {"alpha": 1.0, "content": {"render_type": "solid",
                                               "solid": {"alpha": 1.0, "color": c0}}},
            "strokes": [],
        }],
        "words": words,
        "text": text,
        "is_rich_text": True,
    }

    material = {
        "id": new_id(),
        "content": json.dumps(content_json, ensure_ascii=False),
        "typesetting": 0, "alignment": 1,
        "letter_spacing": 0.0, "line_spacing": 0.02,
        "line_feed": 1, "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": 7, "type": "subtitle", "global_alpha": 1.0,
    }
    draft.content.setdefault("materials", {}).setdefault("texts", []).append(material)
    draft._mat_index = None

    template_seg = _text_segment_template(draft)
    seg = copy.deepcopy(template_seg) if template_seg else {
        "enable_adjust": True, "visible": True, "common_keyframes": [], "keyframe_refs": [],
        "source_timerange": None, "speed": 1.0, "volume": 1.0, "extra_material_refs": [],
        "is_tone_modify": False, "uniform_scale": {"on": True, "value": 1.0},
        "clip": {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
                 "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": -0.78}},
    }
    seg["id"] = new_id()
    seg["material_id"] = material["id"]
    seg["target_timerange"] = {"start": to_us(t0), "duration": to_us(t1 - t0)}
    seg["render_index"] = 15000
    seg["common_keyframes"] = []
    seg["keyframe_refs"] = []
    seg["extra_material_refs"] = []
    if y is not None:
        seg.setdefault("clip", {}).setdefault("transform", {})["y"] = float(y)

    track = _subtitle_track(draft)
    track.raw["segments"].append(seg)
    track.raw["segments"].sort(key=lambda s: s.get("target_timerange", {}).get("start", 0))
    draft._tracks = None

    return {
        "op": "add_per_char_text", "text": text,
        "range": [round(t0, 3), round(t1, 3)],
        "material": material["id"], "segment": seg["id"],
        "words": len(words), "轨道": track.id,
    }


# ---------- v3.1 引擎层四件套：绿幕 chroma-key / 蒙板六类 / 轨道层级 11 层 / 复合片段 Group ----------


def add_chroma_key(draft: Draft, segment_id: str, color: str,
                   strength: float = 0.5, *, edge_feather: float = 0.0,
                   edge_cleanup: float = 0.0, shadow: float = 0.0) -> dict:
    """给视频片段加色度键抠像（绿幕/蓝幕，v3.1 引擎扩展）。

    素材字段严格对齐 capcut-cli-renezander ``src/chroma.ts``：
    ``materials.chromas[] = {id, type:"chromas", color, intensity, shadow, path:""}``，
    并把素材 id 挂到片段的 ``extra_material_refs``。

    Args:
        segment_id: 目标视频片段 id（支持前缀匹配）。
        color: 幕布色 ``'#RRGGBB'`` 或预设名 ``绿幕/蓝幕/白幕``。
        strength: 抠像强度 0-1（映射到 intensity），默认 0.5。
        edge_feather: 边缘羽化（保留参数；renezander 参考无对应字段，不写入 JSON）。
        edge_cleanup: 边缘清理（同上，不写入 JSON）。
        shadow: 阴影 0-1，默认 0。
    """
    from JianYingDraft.video_segment import ChromaKey  # noqa: PLC0415

    seg = _segment_by_id(draft, segment_id)
    color = ChromaKey.PRESETS.get(color, color)
    inst = ChromaKey(color, strength, shadow)
    material = inst.export_json()
    draft.content.setdefault("materials", {}).setdefault("chromas", []).append(material)
    draft._mat_index = None
    refs = seg.raw.setdefault("extra_material_refs", [])
    if material["id"] not in refs:
        refs.append(material["id"])
    return {
        "op": "add_chroma_key", "segment": seg.id,
        "color": color, "intensity": material["intensity"],
        "shadow": material["shadow"], "material": material["id"],
        "edge_feather(未写入)": edge_feather, "edge_cleanup(未写入)": edge_cleanup,
    }


def add_mask(draft: Draft, segment_id: str, mask_type: str, *,
             feather: float = 0.0, invert: bool = False, rotation: float = 0.0,
             round_corner: float = 0.0, size: float = 0.5) -> dict:
    """给视频片段加蒙板（六类：线性/镜面/圆形/矩形/爱心/星形，v3.1）。

    复用底层 ``JianYingDraft.video_segment.Mask`` 构造素材，字段结构与库内
    ``Mask.export_json`` 完全一致（config: aspectRatio/centerX/centerY/feather/
    height/invert/rotation/roundCorner/width + resource_type/resource_id/type="mask"），
    素材写入 ``materials.masks``，id 挂到 ``extra_material_refs``。

    Args:
        segment_id: 目标视频片段 id。
        mask_type: 蒙板类型名（成员名或显示名）：线性/镜面/圆形/矩形/爱心/星形。
        feather: 羽化 0-100，默认 0。
        invert: 是否反转，默认 False。
        rotation: 顺时针旋转角度，默认 0。
        round_corner: 矩形圆角 0-100（仅矩形有效），默认 0。
        size: 蒙板主要尺寸，占素材高度比例，默认 0.5。
    """
    from JianYingDraft.metadata import MaskType  # noqa: PLC0415
    from JianYingDraft.video_segment import Mask  # noqa: PLC0415

    member = _find_member(MaskType, mask_type)
    if member is None:
        raise KeyError(f"没有这个蒙板类型：{mask_type}（可用 {len(MaskType.__members__)} 种）")
    seg = _segment_by_id(draft, segment_id)
    rc = round_corner if member.name == "矩形" else 0.0
    meta = member.value
    m = Mask(meta, 0.0, 0.0, size, size, meta.default_aspect_ratio,
             float(rotation), bool(invert), float(feather) / 100.0, float(rc) / 100.0)
    material = m.export_json()
    draft.content.setdefault("materials", {}).setdefault("masks", []).append(material)
    draft._mat_index = None
    refs = seg.raw.setdefault("extra_material_refs", [])
    refs.append(material["id"])
    return {
        "op": "add_mask", "segment": seg.id, "mask_type": member.name,
        "feather": feather, "invert": bool(invert), "rotation": rotation,
        "round_corner": rc, "material": material["id"],
    }


def validate_track_order(draft: Draft) -> dict:
    """检查草稿轨道顺序是否符合 11 层规范（v3.1）。

    遍历草稿 JSON 中的轨道（数组顺序即先底后顶的 z 序），按 ``track_ranking``
    把轨道 type 映射到层级序号，检查相邻轨道是否非递减。

    Returns:
        dict: ``{"ok": bool, "violations": [...], "ranks": [(name,type,rank), ...]}``。
    """
    from JianYingDraft.track_ranking import track_rank  # noqa: PLC0415

    ranks: list[tuple] = []
    violations: list[dict] = []
    prev = None
    for t in draft.content.get("tracks") or []:
        ttype = t.get("type", "")
        rk = track_rank(ttype)
        ranks.append((t.get("name", ""), ttype, rk))
        if prev is not None and rk < prev[1]:
            violations.append({
                "轨道": t.get("name", ""), "type": ttype, "rank": rk,
                "倒挂于": prev[0], "上轨rank": prev[1],
            })
        prev = (t.get("name", ""), rk)
    return {"ok": len(violations) == 0, "violations": violations, "ranks": ranks}


def combine_segments(draft: Draft, segment_ids: list) -> dict:
    """把多个片段标记为一个复合片段组（v3.1）。

    【实现说明】草稿 JSON 未公开「复合片段 clip」结构（见 ``group_segment.py`` 头注释），
    本函数采用最接近的可行替代：给被选片段写入同一个真实存在的 ``group_id`` 字段，
    并计算组的 bounding 时间范围。**不删除原片段**，依赖素材保持不动，草稿可重新 load。

    Args:
        segment_ids: 要合并的片段 id 列表（支持前缀匹配）。

    Returns:
        dict: ``{"op","group_id","members","range","member_ids"}``。
    """
    from JianYingDraft.group_segment import GroupSegment  # noqa: PLC0415
    from JianYingDraft.time_util import Timerange  # noqa: PLC0415

    found = [_segment_by_id(draft, sid) for sid in segment_ids]
    starts = [int(s.raw.get("target_timerange", {}).get("start", 0)) for s in found]
    ends = [int(s.raw.get("target_timerange", {}).get("start", 0)
                + int(s.raw.get("target_timerange", {}).get("duration", 0))) for s in found]
    g = GroupSegment([s.id for s in found], Timerange(min(starts), max(ends) - min(starts)))
    for s in found:
        s.raw["group_id"] = g.group_id
    return {
        "op": "combine_segments", "group_id": g.group_id,
        "members": len(found),
        "range": [round(min(starts) / 1e6, 3), round(max(ends) / 1e6, 3)],
        "member_ids": [s.id for s in found],
    }


# ---------- v3.1：多层堆叠字幕 + 错峰入场 ----------


def _infer_line_height(material: dict) -> float:
    r"""从文本素材反推行高（clip 归一化单位）。

    公式：line_height = font_size * (1 + line_spacing)，再缩放到 clip 单位。
    font_size 来自 content.styles[0].size（常规约 6.0）；
    line_spacing 来自素材外层字段（常规约 0.02）。
    经验缩放系数 1/40：size=6.0 → line_height ≈ 0.153 clip 单位。
    """
    try:
        content = json.loads(material.get("content") or "{}")
        size = float((content.get("styles") or [{}])[0].get("size", 6.0))
    except (ValueError, IndexError, TypeError):
        size = 6.0
    line_spacing = float(material.get("line_spacing", 0.02))
    return size * (1.0 + line_spacing) / 40.0


def _calculate_stack_y(layer_index: int, max_depth: int, y_center: float,
                        line_height: float) -> float:
    """居中堆叠 Y 坐标：layer 0 在最下，layer max_depth-1 在最上，整体居中于 y_center。"""
    offset = (layer_index - (max_depth - 1) / 2.0) * line_height
    return round(y_center + offset, 6)


def _add_intro_with_delay(draft: Draft, seg_id: str, intro_name: str,
                           delay_us: int, duration_us: int) -> dict:
    """给文本片段加入场动画，可指定入场延迟（stagger）。

    与 add_text_animation 的区别：入场 start 不是 0，而是 delay_us。
    """
    seg = _segment_by_id(draft, seg_id)
    seg_dur_us = int(seg.raw.get("target_timerange", {}).get("duration", 0))
    if seg_dur_us <= 0:
        raise ValueError("片段时长为 0")

    member = _find_text_anim("intro", intro_name)
    if member is None:
        raise KeyError(f"没有这个入场动画：{intro_name}")

    start = max(0, int(delay_us))
    dur = min(int(duration_us or member.value.duration), seg_dur_us - start)
    if dur <= 0:
        raise ValueError(f"延迟 {delay_us}us 后无剩余时长，无法加入场动画")

    item = _anim_item("in", member, start, dur)
    anim_id = new_id()
    mats = draft.content.setdefault("materials", {}).setdefault("material_animations", [])
    mats.append(_build_anim_material(anim_id, [item]))
    refs = seg.raw.setdefault("extra_material_refs", [])
    refs.append(anim_id)
    draft._mat_index = None
    return {"material_animation": anim_id, "start_us": start, "duration_us": dur}


def add_stacked_subtitles(draft: Draft, subtitle_list: list[dict], *,
                           max_stack_depth: int = 2,
                           stagger_us: int = 100000,
                           y_center: float = 0.8,
                           intro_name: str = "向上滑动",
                           line_height: float | None = None) -> dict:
    r"""批量加多层堆叠字幕，按时间重叠自动分配 layer，错峰入场。

    Args:
        subtitle_list: 每项 {text, t0, t1, style?}；style 可选预设名。
        max_stack_depth: 最大堆叠层数（同层内不重叠；超过时自动新建文本轨）。
        stagger_us: 每层入场动画延迟（微秒），layer L 的入场 start = L * stagger_us。
        y_center: 堆叠中心 Y（clip 归一化坐标）。
        intro_name: 入场动画名（默认「向上滑动」）。
        line_height: 行高（clip 单位）；None 时从素材 line_spacing 反推。
    """
    if not subtitle_list:
        return {"op": "add_stacked_subtitles", "added": 0, "layers": 0}

    # 按 t0 排序
    items = sorted(subtitle_list, key=lambda x: float(x["t0"]))

    # 先加一条占位文本，拿到素材模板用于反推行高
    first = items[0]
    style_name = first.get("style")
    rep0 = add_styled_text(
        draft, str(first["text"]), float(first["t0"]), float(first["t1"]),
        style=style_name,
    )
    template_mat = draft.material(rep0["material"])
    if line_height is None:
        line_height = _infer_line_height(template_mat)

    # 清理刚加的占位片段（后面统一按 layer 重新加）
    remove_segment(draft, rep0["segment"], ripple=False)

    # layer 分配：贪心区间图着色
    # layer_last_end[layer] = 该层最后一条字幕的 end 秒
    layer_last_end: list[float] = []
    layer_assignments: list[dict] = []

    for it in items:
        t0 = float(it["t0"])
        t1 = float(it["t1"])
        if t1 <= t0:
            raise ValueError(f"字幕「{it['text']}」t1<=t0")
        assigned = -1
        for L in range(len(layer_last_end)):
            if layer_last_end[L] <= t0 + 0.001:
                assigned = L
                break
        if assigned < 0:
            assigned = len(layer_last_end)
            layer_last_end.append(0.0)
        layer_last_end[assigned] = t1
        layer_assignments.append({**it, "layer": assigned})

    num_layers = len(layer_last_end)
    if num_layers > max_stack_depth:
        # 超过 max_stack_depth 仍允许，但报告
        depth_warning = f"实际层数 {num_layers} > max_stack_depth={max_stack_depth}"
    else:
        depth_warning = None

    # 逐层高加字幕 + Y 坐标 + 错峰入场
    added: list[dict] = []
    for it in layer_assignments:
        L = it["layer"]
        y = _calculate_stack_y(L, num_layers, y_center, line_height)
        rep = add_styled_text(
            draft, str(it["text"]), float(it["t0"]), float(it["t1"]),
            style=it.get("style"),
            y=y,
        )
        # 错峰入场动画
        delay = int(L) * int(stagger_us)
        anim_rep = _add_intro_with_delay(
            draft, rep["segment"], intro_name, delay, 500000)
        added.append({
            "text": it["text"], "layer": L, "y": y,
            "segment": rep["segment"], "track": rep["轨道"],
            "intro_delay_us": delay,
            "intro_material": anim_rep["material_animation"],
        })

    return {
        "op": "add_stacked_subtitles",
        "added": len(added),
        "layers": num_layers,
        "line_height": round(line_height, 4),
        "y_center": y_center,
        "stagger_us": stagger_us,
        "depth_warning": depth_warning,
        "items": added,
    }


# ---------- v3.1：字幕 DP 对齐 + 严格校验（接入草稿） ----------


def align_and_validate_subtitles(draft: Draft,
                                  source_events: list[tuple[float, float]],
                                  *,
                                  max_shift: float = 0.3) -> dict:
    """读草稿字幕 → DP 对齐到源事件 → 严格校验 → 写回时间。

    Args:
        draft: 草稿。
        source_events: 源事件列表 [(start, end), ...]，单位秒。
        max_shift: 单条字幕最大偏移（秒）。

    Returns:
        {"op", "aligned", "validation", "violations"}
    """
    from .subtitle_align import align_subtitles_dp, validate_subtitle_timeline  # noqa: PLC0415

    text_segs = list(draft.text_segments)
    if not text_segs:
        return {"op": "align_and_validate_subtitles", "aligned": 0,
                "validation": {"valid": True, "violations": []},
                "note": "草稿里没有文本片段"}

    # 读取当前时间
    times: list[tuple[float, float]] = [(s.t, s.end) for s in text_segs]

    # DP 对齐
    aligned = align_subtitles_dp(times, source_events, max_shift=max_shift)

    # 写回
    for seg, (new_start, new_end) in zip(text_segs, aligned):
        dur_us = to_us(new_end - new_start)
        seg.raw["target_timerange"] = {"start": to_us(new_start), "duration": dur_us}
    draft._tracks = None

    # 严格校验（对齐后）
    subs_for_validate: list[dict] = []
    for i, (seg, (ns, ne)) in enumerate(zip(text_segs, aligned)):
        subs_for_validate.append({
            "t0": ns, "t1": ne,
            "text": seg.text or "",
            "id": seg.id[:8],
            "source": i,  # 每条字幕的来源索引（对齐后仍一一对应）
        })
    result = validate_subtitle_timeline(subs_for_validate)

    return {
        "op": "align_and_validate_subtitles",
        "aligned": len(aligned),
        "max_shift": max_shift,
        "validation": result,
        "new_times": [[round(ns, 3), round(ne, 3)] for ns, ne in aligned],
    }


# ---------- v3.3 P1-6：字幕自动花字+入场（对照成峰实战的"一键包装"） ----------


def auto_style_subtitles(draft: Draft, *, style_preset: str = "口播标准",
                         intro: str | None = "向上滑动",
                         intro_duration: float = 0.4) -> dict:
    r"""批量给草稿里**已有的全部文本片段**统一加花字预设与入场动画。

    对照成峰实战"字幕+花字+入场动画自动配好"：
      · 入场动画：对每个文本片段调用 add_text_animation 加入场（默认「向上滑动」），
        失败（动画名不存在/片段太短）的单条跳过，不影响其余；
      · 花字预设：记录本次套用的预设名（对**已存在**的富文本/逐字段不重写 content，
        避免破坏 is_rich_text/words；新字幕仍走 add_styled_text(style=...) 套用预设）。

    Args:
        draft: 草稿。
        style_preset: 花字预设名（config/text_styles.json），仅记录与校验，不改已有 content。
        intro: 入场动画名；传 None 则只记录预设、不加动画。
        intro_duration: 入场时长（秒）。

    Returns:
        ``{"op","total","styled","skipped","intro","style_preset","items"}``
    """
    segs = list(draft.text_segments)
    total = len(segs)
    styled: list[str] = []
    skipped: list[str] = []
    anim_names: list[str] = []

    for seg in segs:
        if intro:
            try:
                rep = add_text_animation(draft, seg.id, intro=intro,
                                         intro_duration=intro_duration)
                styled.append(seg.id)
                anim_names.extend(rep.get("动画") or [])
            except Exception as exc:  # noqa: BLE001  单条失败不拖累整体
                skipped.append(f"{seg.id[:8]}:{type(exc).__name__}")
        else:
            styled.append(seg.id)

    return {
        "op": "auto_style_subtitles",
        "total": total,
        "styled": len(styled),
        "skipped": skipped,
        "intro": intro,
        "intro_duration": intro_duration,
        "style_preset": style_preset,
        "note": "花字预设仅记录；已有富文本/逐字字幕不重写 content，新字幕用 add_styled_text(style=预设) 套用",
        "animations": anim_names,
    }
