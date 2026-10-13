r"""brain_draft · 把视频大脑的感知事件，写入剪辑决策单，驱动花字/特效/删减。

把 emotion_peak / scene_change / 长平淡段 映射到 clip 的 花字/特效/滤镜字段，
同时补充 cover_text / cta 候选。产物仍是标准 sheet.json，
所以 build_draft.py / --software 任一路径都能直接消费，剪映用花字特效，
其它软件写 extra 标记。

用法:
    from aicut import brain_draft, video_brain
    brain = video_brain.VideoBrain(video).process_available()  # or start()
    sheet = brain_draft.annotate_sheet(sheet, brain)
"""
from __future__ import annotations

import json
from pathlib import Path

from . import video_brain as vb


def _find_clip_index(clips: list[dict], t: float) -> int | None:
    """返回时间 t 落在第几个 clip 里（按 clips 的 [start,end) 排序）。"""
    for i, c in enumerate(clips):
        start = float(c.get("start", 0))
        end = float(c.get("end", start + 1))
        if start <= t < end:
            return i
    return None


def annotate_sheet(sheet: dict, brain: "vb.VideoBrain") -> dict:
    """在原地插入感知驱动的包装标记，返回新 sheet 影本（不修改原始对象）。"""
    sheet = dict(sheet)  # 浅拷贝
    clips = [dict(c) for c in (sheet.get("clips") or [])]
    hints = brain.packaging_hints()

    # 预扫描事件，收集「情绪高点落点」、「长平淡段」
    emotion_peaks = [h for h in hints if h["理由"] == "情绪高点"]
    flat_runs = [h for h in hints if h["理由"] == "长平淡段"]

    # 1) 情绪峰值 → 对应 clip 加特效标记 + 花字候选
    peak_clip_ids: set[int] = set()
    for h in emotion_peaks:
        idx = _find_clip_index(clips, h["t"])
        if idx is None:
            continue
        c = clips[idx]
        c["effect_peak_time"] = round(h["t"] - float(c.get("start", 0)), 3)
        # 若素材库里有对应花字，挂上；否则塞备注提醒
        c.setdefault("特效", c.get("特效") or "镜头缩放")
        peak_clip_ids.add(idx)
        # 花字候选 = 该clip时间段内关键词（若有逐字稿）
        kw = _keyword_near(brain, h["t"], window=1.5)
        if kw:
            c["花字候选"] = {"词": kw, "位置": "片段内"}

    # 2) 长平淡段 → 标记删除建议 OR 加 B-roll 提示
    for h in flat_runs:
        idx = _find_clip_index(clips, h["t"])
        if idx is None:
            continue
        c = clips[idx]
        c["info_drop_time"] = round(h["t"] - float(c.get("start", 0)), 3)
        c["建议"] = c.get("建议") or "插入 B-roll 或删减"

    sheet["clips"] = clips

    # 3) cover_text / cta 候选（如缺）
    if not sheet.get("cover_text") and emotion_peaks and clips:
        t = emotion_peaks[0]["t"]
        sub = _subtitle_at(brain, t)
        sheet["cover_text"] = sub or "精彩内容即将开始"
    if not sheet.get("cta") and flat_runs:
        sheet["cta"] = "更多精彩点在看"
    return sheet


def _keyword_near(brain: "vb.VideoBrain", t: float, window: float = 1.5) -> str | None:
    """取情绪峰值附件最靠前的字幕文本，用作花字候选（取前几个字）。"""
    segs = _speech_segments(brain.name)
    if not segs:
        return None
    near = [s for s in segs if abs((s["start"] + s["end"]) / 2 - t) <= window]
    if not near:
        return None
    text = near[0]["text"].strip("，。、")
    return text[:6] if text else None


def _subtitle_at(brain: "vb.VideoBrain", t: float) -> str | None:
    segs = _speech_segments(brain.name)
    if not segs:
        return None
    near = [s for s in segs if s["start"] <= t <= s["end"]]
    if near:
        return near[0]["text"].strip("，。、")[:15]
    return None


def _speech_segments(name: str) -> list[dict]:
    tj = vb.ROOT / "02-transcript" / f"{name}.json"
    if not tj.exists():
        # 尝试 .srt
        srt = vb.ROOT / "02-transcript" / f"{name}.srt"
        if srt.exists():
            from .sheet2ir import parse_srt  # noqa: PLC0415
            return parse_srt(srt)
        return []
    try:
        data = json.loads(tj.read_text(encoding="utf-8-sig"))
        return data.get("segments") if isinstance(data, dict) else data
    except json.JSONDecodeError:
        return []
