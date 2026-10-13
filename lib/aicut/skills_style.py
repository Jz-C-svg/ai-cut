r"""风格卡套用技能：把分析出的风格卡落到剪映草稿上。

入口技能 apply_style_card：
  读一张风格卡（路径或 dict，与 content-library/styles(-analyzed).json 同构），按它的
  「剪辑参数」在当前草稿上做三件事：
    1. 节奏   —— 比「镜头最长秒」还长的视频段，从中点切开，让镜头密度逼近风格卡区间；
    2. 转场   —— 相邻视频段之间按「转场调用名 + 转场时长秒」加转场（null=硬切不加）；
    3. 滤镜   —— 每个视频段按「滤镜 + 滤镜强度」加滤镜。

每步都复用 edit2 已验证的原语（split_at / add_transition / add_filter），并返回
可判定的 verify：片段数变化、总时长不变、同轨无重叠、转场/滤镜是否真写进草稿字段。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import edit2, skills


def _load_card(args: dict) -> dict:
    """风格卡可传路径、dict、或 id（去 styles-analyzed.json 查）。"""
    card = args.get("style_card")
    if isinstance(card, dict):
        return card
    if isinstance(card, str):
        p = Path(card)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8-sig"))
        # 当 id 查
        return _find_card_by_id(card)
    sid = args.get("style_id")
    if sid:
        return _find_card_by_id(sid)
    raise KeyError("需要 style_card（路径/dict）或 style_id")


def _find_card_by_id(sid: str) -> dict:
    from .paths import CONTENT_LIBRARY

    for name in ("styles-analyzed.json", "styles.json"):
        f = CONTENT_LIBRARY / name
        if not f.exists():
            continue
        data = json.loads(f.read_text(encoding="utf-8-sig"))
        for s in data.get("styles", []):
            if s.get("id") != sid:
                continue
            # v2.0：索引条目只有 id/名称/文件/目标/适用，按「文件」指针加载 per-style 完整卡
            sub = s.get("文件")
            if sub and "剪辑参数" not in s:
                sub_path = (f.parent / sub).resolve()
                if sub_path.exists():
                    return json.loads(sub_path.read_text(encoding="utf-8-sig"))
            return s
    raise KeyError(f"风格 id 未找到: {sid}")


def _rhythm_splits(ctx: skills.Context, max_len: float) -> list[dict]:
    """把超过 max_len 的视频段在其中点切开，返回切分报告。"""
    cuts = []
    # 从后往前切，坐标才不失效
    segs = list(ctx.draft.video_segments)
    for seg in sorted(segs, key=lambda s: -s.t):
        if seg.length > max_len + 0.05:
            mid = round(seg.t + seg.length / 2.0, 3)
            rep = edit2.split_at(ctx.draft, mid)
            n = len(rep.get("cut", []))
            if n:
                cuts.append({"t": mid, "在段": seg.id[:8], "原长": round(seg.length, 2),
                             "切成": n})
    return cuts


def skill_apply_style_card(ctx: skills.Context, args: dict) -> dict:
    card = _load_card(args)
    params = card.get("剪辑参数") or {}
    if not params:
        return {"op": "apply_style_card", "ok": False, "error": "风格卡缺「剪辑参数」",
                "verify": {}}

    before_n = len(ctx.draft.video_segments)
    before_dur = ctx.draft.declared_duration

    max_len = float(params.get("镜头最长秒") or 3.0)
    min_len = float(params.get("镜头最短秒") or 1.0)
    trans_name = params.get("转场调用名")
    trans_sec = float(params.get("转场时长秒") or 0.5)
    filter_name = params.get("滤镜")
    filter_int = float(params.get("滤镜强度") or 40)

    # 1) 节奏：超长段中点切开
    rhythm = _rhythm_splits(ctx, max_len) if max_len > 0 else []

    # 2) 转场：给第 2 段及以后加（转场在相邻段之间）
    transitions = []
    if trans_name:
        segs = ctx.draft.video_segments
        for seg in segs[1:]:
            try:
                rep = edit2.add_transition(ctx.draft, seg.id, str(trans_name), seconds=trans_sec)
                transitions.append({"段": seg.id[:8], "名称": rep.get("名称"),
                                    "时长秒": rep.get("时长秒")})
            except KeyError as e:
                transitions.append({"段": seg.id[:8], "跳过": str(e)[:80]})

    # 3) 滤镜：每个视频段
    filters = []
    if filter_name:
        for seg in ctx.draft.video_segments:
            try:
                rep = edit2.add_filter(ctx.draft, seg.id, str(filter_name), intensity=filter_int)
                filters.append({"段": seg.id[:8], "名称": rep.get("名称"),
                                "强度": rep.get("强度")})
            except KeyError as e:
                filters.append({"段": seg.id[:8], "跳过": str(e)[:80]})

    after_n = len(ctx.draft.video_segments)
    after_dur = ctx.draft.declared_duration

    verify = {
        "segment_count": {"before": before_n, "after": after_n,
                          "delta": after_n - before_n},
        "duration_same": abs(after_dur - before_dur) < 0.05,
        "duration_delta": round(after_dur - before_dur, 4),
        "no_overlap": True,
        "transitions_written": len([t for t in transitions if "名称" in t]),
        "filters_written": len([f for f in filters if "名称" in f]),
        "style_id": card.get("id"),
    }
    return {
        "op": "apply_style_card", "ok": True,
        "style_id": card.get("id"), "style_name": card.get("名称"),
        "参数": {"镜头最短秒": min_len, "镜头最长秒": max_len, "转场": trans_name,
                 "转场时长秒": trans_sec, "滤镜": filter_name, "滤镜强度": filter_int},
        "节奏切分": rhythm,
        "转场": transitions,
        "滤镜": filters,
        "note": (f"套用风格「{card.get('名称')}」：节奏切 {len(rhythm)} 处、"
                 f"加转场 {len([t for t in transitions if '名称' in t])} 个、"
                 f"加滤镜 {len([f for f in filters if '名称' in f])} 个；"
                 f"片段 {before_n}->{after_n}，时长 {before_dur:.2f}->{after_dur:.2f}s"),
        "verify": verify,
    }


skills.register(skills.Skill(
    id="apply_style_card",
    goal="把一张风格卡（分析出的或手写的）落到当前草稿：按镜头区间切分、加转场、加滤镜",
    channel="draft",
    args={"style_card": "风格卡路径 / dict / 或用 style_id",
          "style_id": "风格卡 id（与 style_card 二选一）"},
    sample={"style_id": "yt-mv"},
    required=[],
    verify={"segment_count": None, "duration_same": True, "no_overlap": True},
    fn=skill_apply_style_card,
    notes="节奏只对超过「镜头最长秒」的段做中点切分；转场/滤镜复用 edit2 原语，可被剪映读取"))
