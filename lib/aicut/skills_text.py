"""文本样式 / 花字 / 文本动画 技能（第三批）。

在 add_subtitle（沿用模板样式）之上，提供：
  add_styled_text    自定义字体/颜色/字号/加粗/描边/背景，或从 config/text_styles.json 取预设
  add_text_animation 给已有文本片段加入场/出场/循环动画

片段指代（segment 参数）与 skills_edit 一致：数字 / id 前缀 / {"t":秒}，
但默认在**文本片段**里找（没有时再退回全部片段）。
"""
from __future__ import annotations

from . import edit2, skills


def _resolve_text_segment(ctx: skills.Context, ref) -> "object":
    """优先在文本片段里解析指代；找不到再在全部片段里找。"""
    pool = ctx.draft.text_segments or list(ctx.draft.all_segments())
    if not pool:
        raise RuntimeError("草稿里没有文本片段")
    if ref is None or ref == "":
        return pool[0]
    if isinstance(ref, bool):
        return pool[0]
    if isinstance(ref, int):
        if 1 <= ref <= len(pool):
            return pool[ref - 1]
        raise KeyError(f"第 {ref} 段文本不存在（共 {len(pool)} 段）")
    if isinstance(ref, dict):
        t = float(ref.get("t", 0))
        for seg in ctx.draft.all_segments():
            if seg.t - 1e-6 <= t < seg.end:
                return seg
        raise KeyError(f"{t:.2f}s 处没有片段")
    text = str(ref).strip()
    if text.isdigit():
        return _resolve_text_segment(ctx, int(text))
    for seg in ctx.draft.all_segments():
        if seg.id == text or seg.id.startswith(text):
            return seg
    raise KeyError(f"找不到片段 {text!r}")


def skill_add_styled_text(ctx: skills.Context, args: dict) -> dict:
    t0, t1 = float(args["t0"]), float(args["t1"])
    text = str(args["text"])
    rep = edit2.add_styled_text(
        ctx.draft, text, t0, t1,
        style=args.get("style"),
        font=args.get("font"),
        color=args.get("color"),
        size=args.get("size"),
        bold=args.get("bold"),
        border_color=args.get("border_color"),
        border_width=args.get("border_width"),
        background_color=args.get("background_color"),
        y=args.get("y"),
    )
    return {**rep, "ok": True,
            "note": (f"加花字「{text[:12]}」{t0:.2f}-{t1:.2f}s "
                     f"字体={rep.get('字体')} 字号={rep.get('字号')}"
                     + (f" 预设={rep['预设']}" if rep.get("预设") else "")),
            "verify": {"text_present": [text], "duration_same": True, "no_overlap": True}}


def skill_add_text_animation(ctx: skills.Context, args: dict) -> dict:
    seg = _resolve_text_segment(ctx, args.get("segment"))
    rep = edit2.add_text_animation(
        ctx.draft, seg.id,
        intro=args.get("intro"),
        outro=args.get("outro"),
        loop=args.get("loop"),
        intro_duration=float(args.get("intro_duration", 0.5)),
        outro_duration=float(args.get("outro_duration", 0.5)),
    )
    return {**rep, "ok": True,
            "note": f"给文本「{(seg.text or '')[:12]}」加动画：" + "、".join(rep["动画"]),
            "verify": {"duration_same": True}}


skills.register(skills.Skill(
    id="add_styled_text", goal="加一条自定义样式文本（花字）：字体/颜色/字号/描边/背景/预设", channel="draft",
    args={"text": "文本内容", "t0": "起点秒", "t1": "终点秒",
          "style": "预设名，如 口播标准 / 房型展示（可加 /字幕 或 /封面）",
          "font": "字体名，如 抖音美好体 / 站酷酷黑体",
          "color": "文字 RGB 0-1 数组 或 '#RRGGBB'", "size": "字号，常规字幕约 6.0",
          "bold": "是否加粗 true/false",
          "border_color": "描边颜色 RGB 数组 或 '#RRGGBB'", "border_width": "描边宽度 0-100",
          "background_color": "背景色 '#RRGGBB'，不传则无背景", "y": "纵向位置，负值向下"},
    sample={"text": "信阳巍阁月子中心", "t0": 0.5, "t1": 3.0, "style": "口播标准/封面", "y": 0.5},
    required=["text", "t0", "t1"],
    verify={"text_present": True, "duration_same": True, "no_overlap": True},
    fn=skill_add_styled_text,
    notes="预设来自 config/text_styles.json；显式参数会覆盖预设"))

skills.register(skills.Skill(
    id="add_text_animation", goal="给已有文本片段加入场/出场/循环动画", channel="draft",
    args={"segment": "哪段文本：数字(第几段文本)/id/{\"t\":秒}",
          "intro": "入场动画名，如 向上滑动/向右擦除/渐显/打字机_I",
          "outro": "出场动画名，如 向上擦除/渐隐",
          "loop": "循环动画名，如 发光模糊多行",
          "intro_duration": "入场时长秒，默认 0.5", "outro_duration": "出场时长秒，默认 0.5"},
    sample={"intro": "向上滑动", "intro_duration": 0.5},
    verify={"duration_same": True}, fn=skill_add_text_animation,
    notes="至少给 intro/outro/loop 之一；动画写入 material_animations"))
