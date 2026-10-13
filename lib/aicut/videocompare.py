"""视频特征比对引擎（v3.7 模板化剪辑引擎 · 模块2）。

两份剪辑指纹 -> 分维度加权相似度打分。权重：
    类型 0.30 / 结构 0.25 / 节奏 0.20 / 内容 0.15 / 镜头模式 0.10

设计：
  · 每维度返回 (0~1 分数, 中文说明)；
  · 内容相似度用标准库 difflib（字符 n-gram 等价物），不引入 jieba；
  · rank() 对候选指纹列表排序，供 find_benchmark 取 Top-N。
"""
from __future__ import annotations

import difflib
import statistics

WEIGHTS = {"type": 0.30, "structure": 0.25, "rhythm": 0.20,
           "content": 0.15, "shot_pattern": 0.10}

# 相近类型族：同族人给 0.6，不同族 0.2
_FAMILIES = {
    "koubo-zhishi": {"koubo-zhishi", "jiaocheng"},
    "jiaocheng": {"koubo-zhishi", "jiaocheng"},
    "duibi": {"duibi", "kadian"},
    "kadian": {"duibi", "kadian"},
    "qingxu-gushi": {"qingxu-gushi", "vlog"},
    "vlog": {"qingxu-gushi", "vlog"},
    "fangtan": {"fangtan"},
    "other": {"other"},
}


def _type_score(fp1: dict, fp2: dict) -> tuple[float, str]:
    t1, t2 = fp1.get("video_type", "other"), fp2.get("video_type", "other")
    if t1 == t2:
        return 1.0, f"同为 {t1}"
    if t2 in _FAMILIES.get(t1, set()):
        return 0.6, f"类型相近（{t1} vs {t2}）"
    return 0.2, f"类型差异较大（{t1} vs {t2}）"


def _structure_score(fp1: dict, fp2: dict) -> tuple[float, str]:
    s1, s2 = fp1.get("structure", {}), fp2.get("structure", {})
    parts = []
    notes = []
    for key, label in (("hook_type", "钩子"), ("body_rhythm", "主体节奏"), ("ending_type", "结尾")):
        v1, v2 = s1.get(key, "other"), s2.get(key, "other")
        if v1 == v2:
            parts.append(1.0)
            notes.append(f"{label}同为{v1}")
        elif v1 == "other" or v2 == "other":
            parts.append(0.5)
            notes.append(f"{label}一方未知")
        else:
            parts.append(0.2)
            notes.append(f"{label}不同（{v1} vs {v2}）")
    score = sum(parts) / len(parts) if parts else 0.0
    return round(score, 3), "；".join(notes)


def _rhythm_score(fp1: dict, fp2: dict) -> tuple[float, str]:
    r1, r2 = fp1.get("rhythm", {}), fp2.get("rhythm", {})
    a1, a2 = float(r1.get("avg_shot", 0) or 0), float(r2.get("avg_shot", 0) or 0)
    s1, s2 = float(r1.get("shot_std", 0) or 0), float(r2.get("shot_std", 0) or 0)
    p1, p2 = r1.get("pace", "other"), r2.get("pace", "other")

    # 平均镜头时长：差值归一化（5s 差异即归零）
    if a1 <= 0 and a2 <= 0:
        avg_score = 0.5
    else:
        diff = abs(a1 - a2)
        avg_score = max(0.0, 1.0 - diff / 5.0)
    # 方差差异：std 差 2s 归零
    std_diff = abs(s1 - s2)
    std_score = max(0.0, 1.0 - std_diff / 2.0)
    # pace 标签
    pace_score = 1.0 if p1 == p2 else (0.5 if {p1, p2} <= {"中速", "快剪", "舒缓"} else 0.2)

    score = 0.5 * avg_score + 0.3 * std_score + 0.2 * pace_score
    note = (f"平均镜头 {a1:.1f}s vs {a2:.1f}s（差{abs(a1-a2):.1f}s），"
            f"方差 {s1:.1f} vs {s2:.1f}，节奏 {p1} vs {p2}")
    return round(score, 3), note


def _content_score(fp1: dict, fp2: dict) -> tuple[float, str]:
    def _blob(fp: dict) -> str:
        sem = fp.get("semantic", {})
        parts = [str(sem.get("主角", "")), str(sem.get("场景", ""))]
        parts += [str(x) for x in (sem.get("叙事摘要") or [])]
        parts += [str(x) for x in (sem.get("关键信息点") or [])]
        return " ".join(parts)

    t1, t2 = _blob(fp1), _blob(fp2)
    if not t1.strip() or not t2.strip():
        return 0.3, "一方内容语义缺失，按低重叠处理"
    ratio = difflib.SequenceMatcher(None, t1, t2).ratio()
    # 取关键信息点的逐对最佳匹配，加权抬升
    pts1 = [str(x) for x in (fp1.get("semantic", {}).get("关键信息点") or [])]
    pts2 = [str(x) for x in (fp2.get("semantic", {}).get("关键信息点") or [])]
    pair_best = []
    for a in pts1:
        if not a:
            continue
        pair_best.append(max(difflib.SequenceMatcher(None, a, b).ratio() for b in pts2) if pts2 else 0.0)
    pt_score = statistics.fmean(pair_best) if pair_best else ratio
    score = 0.5 * ratio + 0.5 * pt_score
    kp1 = "、".join(pts1[:3]) or "无"
    return round(min(1.0, score), 3), f"语义重叠度 {ratio:.2f}；关键信息点 [{kp1}]"


def _shot_pattern_score(fp1: dict, fp2: dict) -> tuple[float, str]:
    p1, p2 = fp1.get("shot_pattern", {}), fp2.get("shot_pattern", {})
    # 主导景别 / 主导运动 相同 + 序列长度比接近
    same_size = 1.0 if p1.get("dominant_size") == p2.get("dominant_size") else 0.4
    same_motion = 1.0 if p1.get("dominant_motion") == p2.get("dominant_motion") else 0.4
    n1, n2 = len(p1.get("size_sequence") or []), len(p2.get("size_sequence") or [])
    if n1 and n2:
        len_sim = min(n1, n2) / max(n1, n2)
    else:
        len_sim = 0.5
    score = 0.4 * same_size + 0.4 * same_motion + 0.2 * len_sim
    note = (f"主景别 {p1.get('dominant_size')}/{p2.get('dominant_size')}，"
            f"主运动 {p1.get('dominant_motion')}/{p2.get('dominant_motion')}，"
            f"镜头数 {n1}/{n2}")
    return round(score, 3), note


_DIM_FUNCS = (
    ("type", _type_score),
    ("structure", _structure_score),
    ("rhythm", _rhythm_score),
    ("content", _content_score),
    ("shot_pattern", _shot_pattern_score),
)


def compare(fp1: dict, fp2: dict) -> dict:
    """两指纹比对：返回分维度加权总分 + 明细 + 自然语言理由。"""
    dimensions = {}
    total = 0.0
    for name, fn in _DIM_FUNCS:
        score, detail = fn(fp1, fp2)
        w = WEIGHTS[name]
        dimensions[name] = {"score": score, "weight": w, "detail": detail}
        total += score * w
    total = round(total, 3)

    reasons, diffs = [], []
    t = dimensions["type"]
    reasons.append(f"类型{t['detail']}")
    if dimensions["structure"]["score"] >= 0.8:
        reasons.append("结构骨架高度一致")
    elif dimensions["structure"]["score"] < 0.5:
        diffs.append("参考视频的钩子/结尾结构与素材不同，需对齐骨架")
    if dimensions["rhythm"]["score"] >= 0.8:
        reasons.append("镜头节奏接近")
    elif dimensions["rhythm"]["score"] < 0.5:
        diffs.append("参考视频镜头切换节奏与素材差异较大，套模板时按素材时长自适应")
    if dimensions["content"]["score"] >= 0.6:
        reasons.append("主题语义有重叠")
    elif dimensions["content"]["score"] < 0.4:
        diffs.append("主题内容不同——只学剪辑结构，不搬内容")

    return {
        "total_score": total,
        "dimensions": dimensions,
        "match_reason": "；".join(reasons) or "结构与节奏整体相近",
        "differences": diffs,
    }


def rank(query_fp: dict, candidates: list[dict]) -> list[dict]:
    """对候选指纹排序。每项追加 comparison 与 total_score，按分降序。"""
    out = []
    for cand in candidates:
        comp = compare(query_fp, cand)
        item = dict(cand)
        item["comparison"] = comp
        item["total_score"] = comp["total_score"]
        out.append(item)
    out.sort(key=lambda x: x["total_score"], reverse=True)
    return out
