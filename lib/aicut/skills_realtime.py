"""实时打点技能：把 ai-auto-editor 的镜头识别结果直接落到草稿上。

技能 `apply_realtime_marks` 做的事：
  读打点 JSON（realtime_bridge.load_marks）
    -> 在每个镜头边界 split_at（edit2，源时间连续、多轨同步切）
    -> （可选）把评分低于阈值的低分镜头整段 ripple_delete
    -> 返回操作报告 + 动态验收规格（切分模式段数+时长不变；删除模式时长差）

为什么不重写 split_at / ripple_delete：这两个是已经自测过的成熟原语
（selftest_edit2 / selftest_core），这里只做"读打点 -> 调原语"的编排。
"""
from __future__ import annotations

from pathlib import Path

from . import edit, edit2, realtime_bridge, skills


def skill_apply_realtime_marks(ctx: skills.Context, args: dict) -> dict:
    marks_path = str(args["marks_path"])
    p = Path(marks_path)
    if not p.exists():
        return {"op": "apply_realtime_marks", "ok": False,
                "error": f"找不到打点文件：{marks_path}", "verify": {}}
    # 支持传入目录：自动取目录下最新的打点 JSON
    if p.is_dir():
        latest = realtime_bridge.latest_marks(p)
        if latest is None:
            return {"op": "apply_realtime_marks", "ok": False,
                    "error": f"目录下没有打点 JSON：{marks_path}", "verify": {}}
        marks_path = str(latest)

    delete_low = bool(args.get("delete_low", False))
    min_score = float(args.get("min_score", realtime_bridge.DEFAULT_MIN_SCORE))

    marks = realtime_bridge.load_marks(marks_path)
    ops = realtime_bridge.marks_to_operations(marks, ctx.draft,
                                               min_score=min_score,
                                               delete_low=delete_low)

    n_shots = len(marks["shots"])
    report: dict = {
        "op": "apply_realtime_marks",
        "marks": marks_path,
        "shots": n_shots,
        "cut_points": marks["cut_points"],
        "splits": [],
        "deleted": [],
        "removed": 0.0,
    }

    # 1) 切分：升序切点逐个 split_at（split_at 对顺序不敏感，升序最可读）
    video_cuts = 0
    for op in ops:
        if op["op"] != "split":
            continue
        rep = edit2.split_at(ctx.draft, float(op["t"]))
        hits = len(rep.get("cut") or [])
        v_hits = sum(1 for c in rep.get("cut") or [] if c.get("track") == "video")
        video_cuts += v_hits
        report["splits"].append({"t": op["t"], "tracks_cut": hits, "video_hits": v_hits,
                                 "note": op.get("note") or ""})

    # 2) 低分删除：ops 里删除项已经按时间从后往前排好，直接顺序执行
    removed = 0.0
    for op in ops:
        if op["op"] != "delete_low_score":
            continue
        a, b = op["range"]
        rep = edit.ripple_delete(ctx.draft, a, b, dry_run=ctx.dry_run)
        removed += (b - a)
        report["deleted"].append({"range": [a, b], "score": op["score"],
                                  "tags": op["tags"],
                                  "deleted_segments": len(rep.get("deleted") or [])})
    report["removed"] = round(removed, 3)

    # 3) 动态验收规格：按"实际动了什么"来断言，而不是拍脑袋
    if not report["deleted"]:
        report["verify"] = {
            "segment_count_delta": {"video": video_cuts},
            "duration_same": True,
            "no_overlap": True,
        }
    else:
        report["verify"] = {
            "duration_delta": [-removed - 0.1, -removed + 0.1],
            "no_overlap": True,
        }

    report["ok"] = True
    report["note"] = (f"{n_shots} 镜头 · {len(report['splits'])} 个切点"
                      + (f" · 删低分 {len(report['deleted'])} 段 {removed:.2f}s" if report["deleted"] else ""))
    return report


skills.register(skills.Skill(
    id="apply_realtime_marks",
    goal="按实时镜头识别打点切分草稿（可选顺手删掉低分镜头）",
    channel="draft",
    args={"marks_path": "打点 JSON 路径（ai-auto-editor 输出的 打点_*.json）",
          "delete_low": "是否整段删掉 score 低于 min_score 的镜头，默认 false",
          "min_score": "低分阈值，默认 0.35"},
    sample={"marks_path": r"data/program_edits/打点_capcut_xxx.json"},
    required=["marks_path"],
    verify={"no_overlap": True},
    fn=skill_apply_realtime_marks,
    notes="切点=每个镜头 end（除最后一个）；打点时间就是时间线时间，直接 split_at；"
          "切分模式验收段数+时长不变，删除模式验收时长差",
))
