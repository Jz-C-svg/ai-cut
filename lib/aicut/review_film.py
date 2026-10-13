# -*- coding: utf-8 -*-
"""成片评审（v3 计划 M3）：七维 rubric。

对一份剪映草稿（及其素材/逐字稿/镜头档案）做结构化评审：
  结构 / 钩子 / 节奏 / 画面 / 字幕 / 声音 / 合规

设计原则（对齐 v3 计划 §6.3）：
  · 全部维度尽量用确定性数据（草稿 IR、静音检测、镜头档案、词表），
    视觉/语义模型只做可选的补强，断网也能出分；
  · 每维 0-100，总分 = 加权平均；
  · 输出每维得分 + 最短板（得分最低维）的定点修复建议，供主体回炉。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import draft as draft_mod
from . import shotprof, skills

# ---------- 合规词表（保守，宁可误报不可漏报） ----------

ABSOLUTE_PATTERNS = [
    r"最[好快强优大]?", r"第一", r"100%", r"百分之百", r"包[治好愈]",
    r"绝对", r"必[然定能]", r"一定[会能]?",
]
MEDICAL_CLAIMS = [
    r"治疗", r"治愈", r"根治", r"药效", r"疗效", r"处方", r"抗[菌炎病毒]",
    r"杀菌", r"消毒", r"医疗", r"医生建议", r"医院",
]
QUALIFICATION_CLAIMS = [
    r"资质", r"认证", r"授权", r"国家[级颁]?", r"专家[推荐]?",
]
ALL_PATTERNS: list[tuple[str, str]] = (
    [("绝对化", p) for p in ABSOLUTE_PATTERNS]
    + [("医疗断言", p) for p in MEDICAL_CLAIMS]
    + [("资质承诺", p) for p in QUALIFICATION_CLAIMS]
)

# 字幕行宽上限（按 CJK 字符数）
SUBTITLE_MAX_CHARS = 22
SUBTITLE_MIN_DWELL = 1.0
MIN_CLIP_LEN = 2.0
FRAGMENT_LEN = 1.8
HOOK_WINDOW = 3.0

# ---------- Editor-Critic 迭代循环（TOP1） ----------
# verdict 阈值：total >= 此分 且 无 critical issue → render（可终态），否则 feedback（回炉）
RENDER_TOTAL_MIN = 80.0
# issue severity 与维度分的映射边界
CRITICAL_BELOW = 40   # 低于此分 = critical
MAJOR_BELOW = 60      # 低于此分 = major；再低 = minor
# 维度分 → 建议技能（suggested_action 必须在 skills.get_registry_ids() 里，防幻觉）
DIM_ACTION = {
    "字幕": ("add_subtitle", {"text": "钩子承诺句", "t0": 0.3, "t1": 3.0}),
    "结构": ("add_subtitle", {"text": "钩子承诺句", "t0": 0.3, "t1": 3.0}),
    "钩子": ("add_subtitle", {"text": "钩子承诺句", "t0": 0.3, "t1": 3.0}),
    "节奏": ("delete_silences", {}),
    "声音": ("delete_silences", {}),
    "画面": ("manual", {}),
    "合规": ("manual", {}),
}


def _text_of(seg) -> str:
    raw = seg.raw or {}
    content = raw.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text", "") or "")
    return ""


def _subtitle_lines(draft) -> list[dict]:
    out = []
    for s in sorted(draft.text_segments, key=lambda x: x.t):
        txt = _text_of(s).strip()
        if txt:
            out.append({"t": s.t, "end": s.end, "length": s.length, "text": txt})
    return out


def _clips(draft) -> list[dict]:
    return [{"t": s.t, "end": s.end, "length": s.length} for s in draft.video_segments]


def _score_structure(subs: list[dict], duration: float) -> tuple[int, list[str]]:
    """结构：钩子→展开→落点是否齐。用字幕时间分布近似。"""
    notes: list[str] = []
    if not subs:
        return 20, ["没有任何字幕，无法判断结构（可能没压字幕）"]
    has_hook = subs[0]["t"] <= HOOK_WINDOW
    has_ending = duration > 0 and subs[-1]["end"] >= max(0.0, duration - 6.0)
    body = len(subs) >= 3
    score = 100
    if not has_hook:
        score -= 30
        notes.append("首条字幕没出现在前 3 秒，缺钩子位置")
    if not has_ending:
        score -= 25
        notes.append("最后一条字幕离片尾较远，缺落点/CTA 位置")
    if not body:
        score -= 20
        notes.append("字幕只有 1-2 条，结构太单薄")
    if len(notes) == 0:
        notes.append("钩子→展开→落点位置齐全")
    return max(0, score), notes


def _score_hook(subs: list[dict], video: Path | None, profile: dict | None) -> tuple[int, list[str]]:
    """钩子：前 3 秒有字幕承诺 + 首镜头画面清晰。"""
    notes: list[str] = []
    score = 60
    if subs and subs[0]["t"] <= HOOK_WINDOW:
        score += 20
        notes.append(f"前 {subs[0]['t']:.1f}s 有字幕：{subs[0]['text'][:14]}")
    else:
        notes.append("前 3 秒没有字幕承诺")
    shots = (profile or {}).get("shots") or []
    if shots:
        first = shots[0]
        r = (first.get("理解") or {}).get("可辨认度")
        if isinstance(r, (int, float)) and r >= 0.6:
            score += 20
            notes.append(f"首镜头可辨认度 {r:.2f}，画面清晰可用")
        else:
            notes.append(f"首镜头可辨认度 {r if isinstance(r, (int, float)) else '未知'}，画面偏弱")
    else:
        notes.append("无镜头档案，画面维度未评估")
    return min(100, score), notes


def _rhythm_hard_rules(profile: dict | None, clips: list[dict]) -> tuple[int, list[dict], list[str]]:
    """TOP4：基于 shotprof 结构化三槽位的节奏硬规则。

    返回 (扣分, 触发的 issues, 追加的 notes)。没有结构化数据时返回 (0, [], [])。
    规则（来源 L-Storyboard 封闭分类法 + EditDuet 节奏直觉）：
      R1 前 5 个镜头全是 medium+locked → 节奏拖沓，扣 10，建议 add_transition/split_at
      R2 镜头时长方差 < 0.5（全是等长镜头）→ 节奏单调，扣 5
      R3 无 close_up（且内容像口播）→ 缺特写，扣 5
    """
    deductions = 0
    hard_issues: list[dict] = []
    extra_notes: list[str] = []
    shots_struct = []
    if profile:
        for s in profile.get("shots") or []:
            sz = s.get("shot_size")
            mo = s.get("shot_motion")
            if sz or mo:
                shots_struct.append(s)
    if not shots_struct:
        return 0, [], []
    # R1：前 5 个镜头全 medium + locked
    head = shots_struct[:5]
    if len(head) >= 3 and all(
        (s.get("shot_size") == "medium" and s.get("shot_motion") in ("locked", "static"))
        for s in head
    ):
        deductions += 10
        extra_notes.append(f"前 {len(head)} 个镜头全是中景+固定机位，节奏拖沓")
        hard_issues.append({
            "type": "节奏", "severity": "major", "location": "0-" +
                f"{round((head[-1].get('time_range') or [0, head[-1].get('t1', 0)])[1], 1)}s",
            "suggested_action": "add_transition",
            "suggested_args": {"name": "模糊", "seconds": 0.5},
            "reason": "前 5 个镜头全是中景+固定，缺视觉变化",
        })
    # R2：镜头时长方差 < 0.5
    lengths = []
    for s in shots_struct:
        tr = s.get("time_range")
        if isinstance(tr, list) and len(tr) == 2:
            lengths.append(float(tr[1]) - float(tr[0]))
        elif s.get("len"):
            lengths.append(float(s["len"]))
    if len(lengths) >= 4:
        mean = sum(lengths) / len(lengths)
        var = sum((x - mean) ** 2 for x in lengths) / len(lengths)
        if var < 0.5:
            deductions += 5
            extra_notes.append(f"镜头时长方差 {var:.2f}<0.5，全是等长镜头，节奏单调")
            hard_issues.append({
                "type": "节奏", "severity": "minor", "location": "全片",
                "suggested_action": "split_at",
                "suggested_args": {"t": round(mean, 2)},
                "reason": "所有镜头等长，切一镜制造节奏变化",
            })
    # R3：无 close_up（内容像口播 = 有字幕且镜头数少）
    has_close = any(s.get("shot_size") in ("close_up", "extreme_close_up") for s in shots_struct)
    if not has_close and clips and len(clips) <= 6:
        deductions += 5
        extra_notes.append("全片无特写镜头，口播缺情绪锚点")
        hard_issues.append({
            "type": "节奏", "severity": "minor", "location": "全片",
            "suggested_action": "manual",
            "suggested_args": {},
            "reason": "建议后续补一个 close-up 镜头（当前技能表无近景裁切原语）",
        })
    return deductions, hard_issues, extra_notes


def _score_rhythm(clips: list[dict], profile: dict | None = None) -> tuple[int, list[str], list[dict]]:
    """节奏：镜头长度分布 + 碎镜头惩罚 + 长拖惩罚 + TOP4 结构化硬规则。

    返回 (score, notes, hard_issues)。hard_issues 是结构化三槽位触发的 issue 草稿。
    """
    notes: list[str] = []
    if not clips:
        return 50, ["没有视频片段"], []
    lengths = [c["length"] for c in clips]
    avg = sum(lengths) / len(lengths)
    frags = [c for c in clips if c["length"] < FRAGMENT_LEN]
    longs = [c for c in clips if c["length"] > 30]
    score = 100
    if frags:
        score -= min(40, 12 * len(frags))
        notes.append(f"{len(frags)} 个碎镜头（<{FRAGMENT_LEN}s）：" +
                     "、".join(f"{c['t']:.1f}s" for c in frags[:5]))
    if longs:
        score -= 20
        notes.append(f"{len(longs)} 个长镜头（>30s），节奏拖沓")
    if avg < 2.5:
        score -= 15
        notes.append(f"平均镜头 {avg:.1f}s，切得太碎")
    if not notes:
        notes.append(f"平均镜头 {avg:.1f}s，节奏正常")
    # TOP4 结构化硬规则
    deductions, hard_issues, extra = _rhythm_hard_rules(profile, clips)
    score -= deductions
    notes.extend(extra)
    return max(0, score), notes, hard_issues


def _score_picture(profile: dict | None) -> tuple[int, list[str]]:
    """画面：镜头档案的模糊/过曝/可辨认度。"""
    notes: list[str] = []
    if not profile or not profile.get("shots"):
        return 60, ["无镜头档案，画面维度未评估"]
    shots = profile["shots"]
    weak = 0
    total_r = 0.0
    counted = 0
    for s in shots:
        u = s.get("理解") or {}
        r = u.get("可辨认度")
        if isinstance(r, (int, float)):
            total_r += r
            counted += 1
            if r < 0.45:
                weak += 1
        if s.get("指标", {}).get("grade") == "糊":
            weak += 1
    score = 100
    if weak:
        score -= 25 * weak
        notes.append(f"{weak} 个镜头画面弱（糊/可辨认度低）")
    if counted:
        avg_r = total_r / counted
        if avg_r < 0.6:
            score -= 20
            notes.append(f"平均可辨认度 {avg_r:.2f}，整体画面偏弱")
        else:
            notes.append(f"平均可辨认度 {avg_r:.2f}")
    conflicts = (profile.get("summary") or {}).get("safe_area_conflicts") or []
    if conflicts:
        score -= 15
        notes.append(f"{len(conflicts)} 个镜头字幕与主体冲突")
    if not notes:
        notes.append("画面清晰、主体可辨认")
    return max(0, score), notes


def _score_subtitles(subs: list[dict]) -> tuple[int, list[str]]:
    """字幕：行宽、停留时长、断句。"""
    notes: list[str] = []
    if not subs:
        return 30, ["没有字幕"]
    too_wide = [s for s in subs if len(s["text"]) > SUBTITLE_MAX_CHARS]
    too_short = [s for s in subs if s["length"] < SUBTITLE_MIN_DWELL]
    score = 100
    if too_wide:
        score -= 8 * len(too_wide)
        notes.append(f"{len(too_wide)} 条超行宽（>{SUBTITLE_MAX_CHARS}字）：" +
                     "、".join(s["text"][:10] for s in too_wide[:3]))
    if too_short:
        score -= 6 * len(too_short)
        notes.append(f"{len(too_short)} 条停留过短（<{SUBTITLE_MIN_DWELL}s）")
    if not notes:
        notes.append(f"{len(subs)} 条字幕，行宽与停留正常")
    return max(0, score), notes


def _score_audio(draft, video: Path | None) -> tuple[int, list[str]]:
    """声音：静音比例（草稿时长内）。"""
    notes: list[str] = []
    duration = draft.declared_duration or 0.0
    if duration <= 0:
        return 50, ["草稿时长未知"]
    if not video or not video.exists():
        return 70, ["素材不存在，未做静音检测"]
    try:
        silences = skills.detect_silences(video, -30.0, 0.5)
    except Exception as exc:  # noqa: BLE001
        return 60, [f"静音检测失败：{str(exc)[:80]}"]
    total_sil = sum(min(e, duration) - max(s, 0.0) for s, e in silences
                    if min(e, duration) > max(s, 0.0))
    ratio = total_sil / duration if duration else 0.0
    score = 100
    if ratio > 0.15:
        score -= 40
        notes.append(f"静音占比 {ratio:.0%}，有长停顿需要剪")
    elif ratio > 0.05:
        score -= 15
        notes.append(f"静音占比 {ratio:.0%}，略多")
    else:
        notes.append(f"静音占比 {ratio:.0%}，声音紧凑")
    return max(0, score), notes


def _score_compliance(subs: list[dict]) -> tuple[int, list[str]]:
    """合规：绝对化/医疗/资质词表命中。"""
    hits: list[str] = []
    for s in subs:
        for cat, pat in ALL_PATTERNS:
            if re.search(pat, s["text"]):
                hits.append(f"[{cat}]「{s['text'][:16]}」")
    if not hits:
        return 100, ["未命中已知风险表达（不代表平台必然通过）"]
    score = max(0, 100 - 25 * len(set(hits)))
    return score, hits[:6]


WEIGHTS = {
    "结构": 0.18, "钩子": 0.18, "节奏": 0.16, "画面": 0.12,
    "字幕": 0.12, "声音": 0.14, "合规": 0.10,
}


def review(draft: str | Path | draft_mod.Draft, *, video: str | Path | None = None,
           use_vision: bool = True) -> dict:
    """评审一份草稿。video 缺省时从草稿第一个视频片段反推。"""
    if isinstance(draft, draft_mod.Draft):
        d = draft
    else:
        d = draft_mod.Draft.load(draft)
    vpath: Path | None = None
    if video:
        vpath = Path(video)
    elif d.video_segments:
        try:
            p = Path(d.video_segments[0].material_path())
            vpath = p if p.exists() else None
        except Exception:  # noqa: BLE001
            vpath = None

    profile = None
    if use_vision and vpath and vpath.exists():
        try:
            profile = shotprof.build(vpath, max_shots=12, use_cloud=True,
                                     progress=lambda *_: None)
        except Exception as exc:  # noqa: BLE001
            profile = {"error": str(exc)[:120]}

    subs = _subtitle_lines(d)
    clips = _clips(d)
    duration = d.declared_duration

    scores: dict[str, dict] = {}
    scores["结构"] = dict(zip(("score", "notes"), _score_structure(subs, duration)))
    scores["钩子"] = dict(zip(("score", "notes"), _score_hook(subs, vpath, profile)))
    _rhythm_score, _rhythm_notes, hard_issues = _score_rhythm(clips, profile)
    scores["节奏"] = {"score": _rhythm_score, "notes": _rhythm_notes}
    scores["画面"] = dict(zip(("score", "notes"), _score_picture(profile)))
    scores["字幕"] = dict(zip(("score", "notes"), _score_subtitles(subs)))
    scores["声音"] = dict(zip(("score", "notes"), _score_audio(d, vpath)))
    scores["合规"] = dict(zip(("score", "notes"), _score_compliance(subs)))

    total = sum(scores[k]["score"] * WEIGHTS[k] for k in WEIGHTS)
    weakest = min(WEIGHTS, key=lambda k: scores[k]["score"])
    strongest = max(WEIGHTS, key=lambda k: scores[k]["score"])

    issues = _build_issues(scores, subs, clips, hard_issues)
    critical = [i for i in issues if i["severity"] == "critical"]
    verdict = "render" if (total >= RENDER_TOTAL_MIN and not critical) else "feedback"
    return {
        "draft": d.name,
        "duration": round(duration, 2),
        "total": round(total, 1),
        "dimensions": {k: {"score": v["score"], "notes": v["notes"]} for k, v in scores.items()},
        "weakest": {"dimension": weakest, "score": scores[weakest]["score"],
                    "advice": _advice_for(weakest, scores, subs, profile)},
        "strongest": strongest,
        "video": str(vpath) if vpath else None,
        "vision_error": (profile or {}).get("error"),
        # ---- Editor-Critic 迭代循环（TOP1）新增字段，原有字段全部保留 ----
        "verdict": verdict,
        "issues": issues,
        "iteration": 1,
        "render_threshold": RENDER_TOTAL_MIN,
    }


def _validate_action(action: str) -> str:
    """suggested_action 必须在 skills.REGISTRY 里；不存在降级为 manual 并记 warning。"""
    try:
        ids = set(skills.get_registry_ids())
    except Exception:  # noqa: BLE001
        ids = set()
    if action in ids:
        return action
    return "manual"


def _severity_for(score: int) -> str:
    if score < CRITICAL_BELOW:
        return "critical"
    if score < MAJOR_BELOW:
        return "major"
    return "minor"


def _build_issues(scores: dict, subs: list[dict], clips: list[dict],
                  hard_issues: list[dict] | None = None) -> list[dict]:
    """从七维分数 + notes 推导结构化 issue 列表（供 Editor 回炉执行）。

    每个 issue：
      type            维度中文名
      severity        critical/major/minor
      location        可定位位置（0-3s / 全片 / 片段N）
      suggested_action  必须在 skills.REGISTRY 里（不在降级 manual）
      suggested_args    建议参数（可选）
      reason          为什么这是个问题
    """
    issues: list[dict] = list(hard_issues or [])
    # 合规：词表命中 = critical
    comp_notes = scores.get("合规", {}).get("notes", []) or []
    hits = [n for n in comp_notes if n.startswith("[")]
    if hits:
        issues.append({
            "type": "合规", "severity": "critical", "location": "全片",
            "suggested_action": "manual", "suggested_args": {},
            "reason": "命中风险表达：" + "；".join(hits[:3]),
        })
    # 字幕：无字幕 = critical，否则按分数
    if not subs:
        action, args = DIM_ACTION["字幕"]
        issues.append({
            "type": "字幕", "severity": "critical", "location": "全片",
            "suggested_action": _validate_action(action),
            "suggested_args": args,
            "reason": "草稿没有任何字幕，结构/钩子/字幕三维同时崩",
        })
    else:
        s = scores.get("字幕", {}).get("score", 100)
        if s < MAJOR_BELOW:
            action, args = DIM_ACTION["字幕"]
            issues.append({
                "type": "字幕", "severity": _severity_for(s), "location": "全片",
                "suggested_action": _validate_action(action),
                "suggested_args": args,
                "reason": (scores["字幕"]["notes"][0] if scores["字幕"]["notes"]
                           else "字幕维度得分偏低"),
            })
    # 其余维度按分
    for dim in ("结构", "钩子", "节奏", "画面", "声音"):
        sc = scores.get(dim, {}).get("score", 100)
        if sc >= MAJOR_BELOW:
            continue
        # 节奏维度已经在硬规则里发过 issue，避免重复
        if dim == "节奏" and hard_issues:
            continue
        action, args = DIM_ACTION.get(dim, ("manual", {}))
        reason = (scores[dim]["notes"][0] if scores[dim].get("notes")
                  else f"{dim}维度得分 {sc}")
        location = "全片"
        if dim == "钩子" and subs:
            location = f"0-{HOOK_WINDOW:.0f}s"
        issues.append({
            "type": dim, "severity": _severity_for(sc), "location": location,
            "suggested_action": _validate_action(action),
            "suggested_args": dict(args),
            "reason": reason,
        })
    return issues


def _advice_for(dim: str, scores: dict, subs: list[dict], profile: dict | None) -> str:
    """最短板给出可执行的定点修复建议（对齐 v3 §6.3）。"""
    if dim == "钩子":
        if subs and subs[0]["t"] > HOOK_WINDOW:
            return f"钩子弱：首条字幕在 {subs[0]['t']:.1f}s 才出现，建议把承诺句压到前 {HOOK_WINDOW:.0f}s 内"
        return "钩子弱：画面偏弱，建议换用可辨认度最高的镜头（见档案 hook_candidates）做开场"
    if dim == "结构":
        return "结构不完整：补足 钩子(前3s)-展开-落点/CTA，且只保留一个 CTA"
    if dim == "节奏":
        return "节奏问题：删掉碎镜头或合并短片段，控制平均镜头在 3-6s（按风格卡）"
    if dim == "画面":
        return "画面偏弱：低可辨认度/糊镜头改用 B-roll 覆盖，或直接删除（见档案 weak_shots）"
    if dim == "字幕":
        return "字幕问题：拆长句到 22 字内、延长停留到 1s+，并避开安全区冲突"
    if dim == "音频":
        return "声音问题：剪掉长静音区间，压缩停顿（delete_silences 技能）"
    if dim == "合规":
        return "合规风险：命中绝对化/医疗/资质词，替换为可核验表述"
    return "检查该维度数据来源"


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="成片评审（七维 rubric）")
    ap.add_argument("draft", help="草稿名或草稿目录")
    ap.add_argument("--video", default=None, help="素材视频路径（缺省从草稿反推）")
    ap.add_argument("--no-vision", action="store_true", help="跳过镜头档案（纯规则评审）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    res = review(args.draft, video=args.video, use_vision=not args.no_vision)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    print(f"评审：{res['draft']} · 时长 {res['duration']}s · 总分 {res['total']}/100")
    for k, v in res["dimensions"].items():
        print(f"  {k}: {v['score']:>3}  {'；'.join(v['notes'])[:80]}")
    w = res["weakest"]
    print(f"\n最短板：{w['dimension']}（{w['score']}）")
    print(f"建议：{w['advice']}")


if __name__ == "__main__":
    main()
