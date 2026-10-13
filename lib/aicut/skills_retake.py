"""双道技能：重说口误 + 语义 filler 去除（在能量道之上的"语义两道"）。

三道合一（实际是三通道，注册名为 delete_filler_retakes）：
  能量道  detect_silences（已有，ffmpeg silencedetect）——算区间不直接删；
  重说道  retake.detect_retakes（整句相似 / 前缀卡壳）；
  filler道 filler.detect_fillers（词表 + 连续重复），可选 LLM 语义道并集。

所有区间先在「素材时间」上检测，再经 timeline_spans 映射到「时间线时间」，
合并相邻区间、两侧留白后，从后往前 ripple_delete（防止坐标失效）。

AI 通道只走本地免费 llm（llm.chat, auto=Ollama 优先），不引入付费 Key。
"""
from __future__ import annotations

from . import edit
from . import retake as _retake
from . import filler as _filler
from . import perception as _perception
from .skills import (
    Context, Skill, register, detect_silences, load_transcript,
    timeline_spans, _video_of, _name_of,
)


def _segments_of(transcript: dict) -> list[dict]:
    """逐字稿 -> [{start, end, text}]，兼容字段名。"""
    out = []
    for s in transcript.get("segments") or []:
        st = s.get("start", s.get("起", 0))
        en = s.get("end", s.get("止", 0))
        txt = (s.get("text") or s.get("句") or "").strip()
        try:
            st, en = float(st), float(en)
        except (TypeError, ValueError):
            continue
        if en > st and txt:
            out.append({"start": st, "end": en, "text": txt})
    return out


def _words_of(transcript: dict) -> list[dict]:
    """逐字稿 -> 词级 [{start, end, word}]。没有词级时间戳返回空列表。"""
    out = []
    for w in transcript.get("words") or []:
        st = w.get("start", w.get("st"))
        en = w.get("end", w.get("et"))
        txt = w.get("word") or w.get("text") or ""
        try:
            st, en = float(st), float(en)
        except (TypeError, ValueError):
            continue
        if en > st and str(txt).strip():
            out.append({"start": st, "end": en, "word": str(txt)})
    return out


def run_delete_filler_retakes(ctx: Context, args: dict) -> dict:
    """统一执行器：能量道 + 重说道 + filler道 合并删除。

    参数：
      use_retake   bool   默认 True，是否跑重说检测；
      use_filler   bool   默认 True，是否跑词表 filler；
      use_semantic bool   默认 False，是否再调 LLM 语义道（与词表道取并集）；
      filler_words list   自定义 filler 词表（None 用默认）；
      pad_ms       int    删除区间两侧留白毫秒，默认 50；
      with_silence bool   默认 True，是否跑能量道（静音）；
      db / min_len        能量道阈值（默认走 perception 预设）。
    """
    use_retake = bool(args.get("use_retake", True))
    use_filler = bool(args.get("use_filler", True))
    use_repeated_phrases = bool(args.get("use_repeated_phrases", True))
    use_semantic = bool(args.get("use_semantic", False))
    with_silence = bool(args.get("with_silence", True))
    review_mode = bool(args.get("review_mode", False))
    pad_ms = float(args.get("pad_ms", 50))
    pad = pad_ms / 1000.0
    filler_words = args.get("filler_words")  # None 则用默认
    warnings: list[str] = []

    transcript = ctx.transcript or load_transcript(_name_of(ctx))
    if not transcript:
        return {"op": "delete_filler_retakes", "skipped": "没有逐字稿（ctx.transcript / 02-transcript）",
                "verify": {}}

    segs = _segments_of(transcript)
    words = _words_of(transcript)
    if not words:
        warnings.append("逐字稿无词级时间戳（words），filler 降级为句段粗粒度")

    # ---- 各道在「素材时间」上的检测结果 ----
    src_spans: list[tuple[float, float]] = []
    channels: dict[str, list] = {"retake": [], "filler": [], "silence": [],
                                 "semantic": [], "repeated_phrases": []}

    # 第一道：能量道（静音）
    if with_silence:
        db = float(args.get("db") or _perception.get("silence_db"))
        min_len = float(args.get("min_len") or _perception.get("silence_min_len"))
        video = _video_of(ctx)
        if video:
            for span in detect_silences(video, db, min_len):
                channels["silence"].append(span)
                src_spans.append(span)

    # 第二道：句级重说（Levenshtein 整句/前缀）
    if use_retake and segs:
        hits = _retake.detect_retakes(segs)
        channels["retake"] = hits
        for h in hits:
            src_spans.append((h["remove_start"], h["remove_end"]))

    # 第二道·补充：词/字级重复短语（SmartCut 思路，与 Levenshtein 取并集）
    if use_repeated_phrases and segs:
        rp = _retake.detect_repeated_phrases(segs)
        channels["repeated_phrases"] = rp
        for h in rp:
            src_spans.append((h["remove_start"], h["remove_end"]))

    # 第三道：filler（词表/重复 规则道）
    if use_filler:
        if words:
            det = _filler.detect_fillers(words, filler_words)
        else:
            # 降级：句段当"词"做粗粒度匹配
            pseudo = [{"start": s["start"], "end": s["end"], "word": s["text"]} for s in segs]
            det = _filler.detect_fillers(pseudo, filler_words)
        channels["filler"] = det
        for d in det:
            src_spans.append((float(d["start"]), float(d["end"])))

        # 第三道·语义增强：LLM 标 filler，与规则道取并集
        if use_semantic:
            sem = _filler.detect_fillers_semantic(transcript, provider="auto",
                                                  filler_words=filler_words)
            channels["semantic"] = sem
            for d in sem:
                src_spans.append((float(d["start"]), float(d["end"])))

    # ---- 素材时间 -> 时间线时间 ----
    tl_spans: list[tuple[float, float]] = []
    for a, b in src_spans:
        mapped = timeline_spans(ctx.draft, (a, b))
        if mapped:
            tl_spans.append(mapped)

    # 合并相邻 / 重叠区间
    merged = _retake.merge_spans(tl_spans, gap=0.1)

    # 两侧向内留白 pad（不把词切太干净，留呼吸）；太短（<0.1s）不动刀
    planned: list[tuple[float, float]] = []
    for a, b in merged:
        na, nb = a + pad, b - pad
        if nb - na >= 0.1:
            planned.append((round(na, 3), round(nb, 3)))
    planned.sort(reverse=True)  # 从后往前删，前面的坐标才不失效

    # A2：给各道命中补「口误分类」（重复/卡顿/语气词/长静音），供审查勾选
    for h in channels["retake"]:
        h.setdefault("disfluency", _retake.classify_disfluency(h))
    for h in channels["repeated_phrases"]:
        h.setdefault("disfluency", "重复")
    for d in channels["filler"]:
        d.setdefault("disfluency", _retake.classify_disfluency(d))
    for d in channels["semantic"]:
        d.setdefault("disfluency", "语气词")

    # A2 review_mode：只返回待删区间（带分类+原因），不执行删除，供人工勾选确认
    if review_mode:
        to_review = [{"range": [a, b], "duration": round(b - a, 3)} for a, b in reversed(planned)]
        return {
            "op": "delete_filler_retakes",
            "warnings": warnings,
            "review_mode": True,
            "channels": {
                "silence": [[round(a, 3), round(b, 3)] for a, b in channels["silence"]],
                "retake": channels["retake"],
                "filler": channels["filler"],
                "semantic": channels["semantic"],
                "repeated_phrases": channels["repeated_phrases"],
            },
            "source_spans": [[round(a, 3), round(b, 3)] for a, b in src_spans],
            "planned_to_delete": to_review,
            "applied": [],
            "removed": 0.0,
            "verify": {},
        }

    removed = 0.0
    done: list[list[float]] = []
    seg_count_before = len(list(ctx.draft.all_segments())) if ctx.draft else 0
    for a, b in planned:
        edit.ripple_delete(ctx.draft, a, b, dry_run=ctx.dry_run)
        removed += b - a
        done.append([a, b])
    seg_count_after = len(list(ctx.draft.all_segments())) if ctx.draft else 0
    removed = round(removed, 3)

    return {
        "op": "delete_filler_retakes",
        "warnings": warnings,
        "channels": {
            "silence": [[round(a, 3), round(b, 3)] for a, b in channels["silence"]],
            "retake": channels["retake"],
            "filler": channels["filler"],
            "semantic": channels["semantic"],
            "repeated_phrases": channels["repeated_phrases"],
        },
        "source_spans": [[round(a, 3), round(b, 3)] for a, b in src_spans],
        "applied": done,
        "removed": removed,
        "segment_count_delta": seg_count_after - seg_count_before,
        "verify": ({"duration_delta": [-removed - 0.1, -removed + 0.1], "no_overlap": True,
                    "segment_count_delta": {"video": seg_count_after - seg_count_before}}
                   if removed > 0 else {}),
    }


def skill_delete_filler_retakes(ctx: Context, args: dict) -> dict:
    return run_delete_filler_retakes(ctx, args)


register(Skill(
    id="delete_filler_retakes",
    goal="剪掉重说口误 + 语义 filler（嗯啊那个/重复口吃），可叠加静音道",
    channel="draft",
    args={
        "use_retake": "是否跑句级重说检测（Levenshtein），默认 true",
        "use_repeated_phrases": "是否跑词/字级重复短语检测（SmartCut 第三道），默认 true",
        "use_filler": "是否跑词表 filler，默认 true",
        "use_semantic": "是否调本地 LLM 语义道，默认 false",
        "review_mode": "只返回待删区间（带口误分类）不执行删除，默认 false",
        "filler_words": "自定义 filler 词表（list，可选）",
        "pad_ms": "删除区间两侧留白毫秒，默认 50",
        "with_silence": "是否叠加能量静音道，默认 true",
    },
    sample={"use_retake": True, "use_filler": True, "pad_ms": 50},
    verify={"no_overlap": True},
    fn=skill_delete_filler_retakes,
    notes="需要逐字稿（02-transcript/<素材>.json）；词级时间戳缺失时 filler 降级句段粗粒度",
))
