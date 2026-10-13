# -*- coding: utf-8 -*-
"""Patch 应用器（TOP1 细化）：只改 Critic 指出的那一段，不全片重渲染。

背景：VEA / EditDuet 的 critic 反馈里，每个 issue 都带一个"定点 patch"
（哪个片段、改什么）。这里把 plan_patch 翻译成一次技能调用，
并把受影响的 segment 定位清楚——避免"为了改一条字幕重剪全片"。

核心：
  parse_segment_ref(ref, draft)  点分路径解析：video.2 / text.3 / {"t":4.5}
  apply_patch(draft, plan_patch, ...)  构造 ctx → 调技能 → 返回改动报告
"""
from __future__ import annotations

from pathlib import Path

from . import skills
from .draft import Draft


def parse_segment_ref(ref, draft: Draft):
    """点分路径解析器。

    支持：
      "video.2"   → 第 2 段视频（从 1 数起）
      "text.3"    → 第 3 段字幕
      "2"         → 第 2 段视频（数字默认 video 轨）
      "a1b2c3"    → segment id 前缀
      {"t": 4.5}  → 覆盖 4.5s 的那段
      None / ""   → 第一段视频
    找不到返回 None（不抛异常，让调用方决定 skip 还是升级人工）。
    """
    if ref is None or ref == "":
        return draft.video_segments[0] if draft.video_segments else None
    if isinstance(ref, int):
        segs = draft.video_segments
        return segs[ref - 1] if 1 <= ref <= len(segs) else None
    if isinstance(ref, dict):
        t = float(ref.get("t", 0))
        for seg in draft.all_segments():
            if seg.t - 1e-6 <= t < seg.end:
                return seg
        return None
    s = str(ref).strip()
    if "." in s:
        kind, idx = s.split(".", 1)
        try:
            i = int(idx)
        except ValueError:
            return None
        segs = draft.segments_of(kind)
        return segs[i - 1] if 1 <= i <= len(segs) else None
    if s.isdigit():
        segs = draft.video_segments
        i = int(s)
        return segs[i - 1] if 1 <= i <= len(segs) else None
    # 否则按 segment id 前缀找
    for seg in draft.all_segments():
        if seg.id == s or seg.id.startswith(s):
            return seg
    return None


def apply_patch(draft: Draft, plan_patch: dict, *, video=None,
                transcript=None, memory=None, log=print) -> dict:
    """执行 Critic 给的 plan_patch。返回改动报告。

    plan_patch: {"skill": "add_subtitle", "args": {...}}
    - skill 不在 REGISTRY → ok=False, error=未知技能
    - 技能执行抛异常 → 捕获后返回 ok=False
    - 点分路径：args.segment 是 "video.2" 这种写法时，解析成具体 segment id
    """
    skill_id = (plan_patch or {}).get("skill", "")
    args = dict((plan_patch or {}).get("args") or {})
    skill = skills.get(skill_id)
    if skill is None:
        return {"ok": False, "error": f"未知技能 {skill_id!r}（不在 REGISTRY）",
                "plan_patch": plan_patch, "changed": False}

    # 点分路径：把 args.segment 从 "video.2" 解析成具体 id（技能侧 _resolve_segment 也支持，
    # 这里提前解析一次，让报告里能直接看到动了哪一段）
    located = None
    if "segment" in args:
        seg = parse_segment_ref(args["segment"], draft)
        if seg is not None:
            located = {"ref": args["segment"], "id": seg.id,
                       "t0": round(seg.t, 3), "t1": round(seg.end, 3)}
            args["segment"] = seg.id  # 技能侧按 id 前缀也能解析

    ctx = skills.Context(
        draft=draft, before=None, video=video, transcript=transcript,
        memory=memory, dry_run=False, log=log,
    )
    try:
        report = skill.run(ctx, args)
    except Exception as exc:  # noqa: BLE001
        report = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    if not isinstance(report, dict):
        report = {"ok": False, "error": f"技能返回非 dict: {type(report).__name__}"}
    report["plan_patch"] = plan_patch
    report["located"] = located
    report["changed"] = bool(report.get("ok") and not report.get("skipped"))
    return report
