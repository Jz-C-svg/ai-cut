r"""精确剪辑技能（第二批）：切分 / 加字幕 / 加转场 / 加滤镜 / 调音量。

每个技能都带**可判定的验收规格**（段数变化、时长不变、无重叠、文本命中），
所以主体做完就知道到底成没成——这是"智能操作"和"瞎改"的分界线。

片段指代（segment 参数）支持四种写法，模型怎么说都能对上：
  不给        → 第一段视频
  数字 2      → 第 2 段（从 1 数起）
  "a1b2c3"   → 片段 id 前缀
  {"t": 4.5} → 覆盖 4.5 秒的那一段
"""
from __future__ import annotations

from . import edit2, skills


def _resolve_segment(ctx: skills.Context, ref) -> "object":
    segs = ctx.draft.video_segments
    if not segs:
        raise RuntimeError("草稿里没有视频片段")
    if ref is None or ref == "":
        return segs[0]
    if isinstance(ref, bool):  # bool 是 int 的子类，先挡掉
        return segs[0]
    if isinstance(ref, int):
        if 1 <= ref <= len(segs):
            return segs[ref - 1]
        raise KeyError(f"第 {ref} 段不存在（视频一共 {len(segs)} 段）")
    if isinstance(ref, dict):
        t = float(ref.get("t", 0))
        for seg in ctx.draft.all_segments():
            if seg.t - 1e-6 <= t < seg.end:
                return seg
        raise KeyError(f"{t:.2f}s 处没有片段")
    text = str(ref).strip()
    if text.isdigit():
        return _resolve_segment(ctx, int(text))
    for seg in ctx.draft.all_segments():
        if seg.id == text or seg.id.startswith(text):
            return seg
    raise KeyError(f"找不到片段 {text!r}")


def skill_split(ctx: skills.Context, args: dict) -> dict:
    t = float(args["t"])
    rep = edit2.split_at(ctx.draft, t)
    n = len(rep["cut"])
    return {**rep, "ok": bool(n), "note": f"在 {t:.2f}s 切开 {n} 处",
            "verify": {"segment_count_delta": {"video": 1}, "duration_same": True,
                       "no_overlap": True} if n else {}}


def skill_add_subtitle(ctx: skills.Context, args: dict) -> dict:
    t0, t1 = float(args["t0"]), float(args["t1"])
    text = str(args["text"])
    rep = edit2.add_subtitle(ctx.draft, text, t0, t1, y=args.get("y"),
                            line_max_width=args.get("line_max_width"),
                            overlap=str(args.get("overlap", "new_track")))
    return {**rep, "ok": True,
            "note": (f"加字幕「{text[:12]}」{t0:.2f}-{t1:.2f}s"
                     + ("（与原字幕重叠，已另起一轨）" if rep.get("落到新轨") else "")),
            "verify": {"text_present": [text], "no_overlap": True, "duration_same": True}}


def skill_add_transition(ctx: skills.Context, args: dict) -> dict:
    seg = _resolve_segment(ctx, args.get("segment"))
    rep = edit2.add_transition(ctx.draft, seg.id, str(args["name"]),
                               seconds=float(args.get("seconds", 0.5)))
    return {**rep, "ok": True,
            "note": f"给第 {ctx.draft.video_segments.index(seg) + 1} 段加转场「{rep['名称']}」{rep['时长秒']}s",
            "verify": {"duration_same": True, "no_overlap": True}}


def skill_add_filter(ctx: skills.Context, args: dict) -> dict:
    seg = _resolve_segment(ctx, args.get("segment"))
    rep = edit2.add_filter(ctx.draft, seg.id, str(args["name"]),
                           intensity=float(args.get("intensity", 50)))
    return {**rep, "ok": True, "note": f"给第 {ctx.draft.video_segments.index(seg) + 1} 段加滤镜"
            f"「{rep['名称']}」强度 {rep['强度']}",
            "verify": {"duration_same": True, "no_overlap": True}}


def skill_set_gain(ctx: skills.Context, args: dict) -> dict:
    seg = _resolve_segment(ctx, args.get("segment"))
    db = float(args["db"])
    edit2.set_gain_db(seg, db)
    return {"op": "set_gain_db", "ok": True, "segment": seg.id, "db": db,
            "volume": seg.raw.get("volume"),
            "note": f"第 {ctx.draft.video_segments.index(seg) + 1} 段音量设为 {db}dB",
            "verify": {"duration_same": True}}


def skill_list_effects(ctx: skills.Context, args: dict) -> dict:
    kind = str(args.get("kind", "转场"))
    keyword = str(args.get("keyword", ""))
    limit = int(args.get("limit", 20))
    items = (edit2.list_transitions(keyword, limit) if kind == "转场"
             else edit2.list_filters(keyword, limit))
    return {"op": "list_effects", "ok": True, "kind": kind, "items": items,
            "note": f"{kind}可用 {len(items)} 条（关键词「{keyword}」）", "verify": {}}


skills.register(skills.Skill(
    id="split_at", goal="在指定秒数切开片段（源时间连续，用于精确分镜）", channel="draft",
    args={"t": "切点秒数"}, sample={"t": 3.9}, required=["t"], verify={"no_overlap": True},
    fn=skill_split, notes="切完左右两段源时间连续；总时长不变"))
skills.register(skills.Skill(
    id="add_subtitle", goal="在指定时间段加一条字幕（样式沿用草稿里已有字幕）", channel="draft",
    args={"text": "字幕文字", "t0": "起点秒", "t1": "终点秒", "y": "纵向位置，默认不动",
          "overlap": "与原字幕重叠时：new_track(默认)/allow"},
    sample={"text": "选月子中心先问这一句", "t0": 0.5, "t1": 3.0},
    required=["text", "t0", "t1"], verify={"no_overlap": True}, fn=skill_add_subtitle))
skills.register(skills.Skill(
    id="add_transition", goal="给视频片段加转场（本机可用 433 种）", channel="draft",
    args={"name": "转场名，如 模糊/闪白/推近", "seconds": "时长秒",
          "segment": "哪一段：数字(第几段)/id/{\"t\":秒}"},
    sample={"name": "模糊", "seconds": 0.5}, required=["name"], verify={"no_overlap": True}, fn=skill_add_transition))
skills.register(skills.Skill(
    id="add_filter", goal="给视频片段加滤镜（本机可用 972 种）", channel="draft",
    args={"name": "滤镜名，如 清晰明亮", "intensity": "强度 0-100",
          "segment": "哪一段：数字(第几段)/id/{\"t\":秒}"},
    sample={"name": "清晰明亮", "intensity": 40}, required=["name"], verify={"no_overlap": True}, fn=skill_add_filter))
skills.register(skills.Skill(
    id="set_gain_db", goal="按分贝调片段音量（-60dB 约等于静音）", channel="draft",
    args={"db": "分贝，如 -6", "segment": "哪一段：数字(第几段)/id/{\"t\":秒}"},
    sample={"db": -6}, required=["db"], verify={"no_overlap": True}, fn=skill_set_gain))
skills.register(skills.Skill(
    id="list_effects", goal="查本机剪映可用的转场/滤镜名（避免猜名字）", channel="query",
    args={"kind": "转场/滤镜", "keyword": "关键词", "limit": "条数，默认 20"},
    sample={"kind": "转场", "keyword": "模糊"}, fn=skill_list_effects))
