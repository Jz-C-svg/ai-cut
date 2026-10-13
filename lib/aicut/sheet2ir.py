r"""sheet2ir · 剪辑决策单 → 多剪辑软件统一 IR（EditorProject）。

把 03-scripts/*.sheet.json 转为 editor_bridges.base.EditorProject，
再交给 editor_bridges.registry.write_project 写成任意剪辑软件的原生工程文件。

支持字段（与剪映决策单同一份格式）：
- project / width / height / fps
- clips: [{file, start, end, speed, 特效?, 滤镜?, 滤镜强度?}]
- subtitles: SRT 路径  或  subtitles_json: 逐字稿 JSON（[{start,end,text}]）
- cover_text / cta: 封面承诺与行动指令（text 轨道）
- bgm / voiceover: {file, volume}
"""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path

from editor_bridges.base import EditorProject, EditorSegment, EditorTrack


def parse_srt(path: str | Path) -> list[dict]:
    """极简 SRT 解析 → [{start, end, text}]（秒）。"""
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    out: list[dict] = []
    block: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n") + [""]:
        if line.strip() == "":
            if block:
                idx = next((i for i, l in enumerate(block) if "-->" in l), None)
                if idx is not None:
                    m = re.match(
                        r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)",
                        block[idx],
                    )
                    if m:
                        g = [int(x) for x in m.groups()]
                        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                        body = "\n".join(block[idx + 1:]).strip()
                        if body:
                            out.append({"start": start, "end": end, "text": body})
                block = []
        else:
            block.append(line)
    return out


def parse_transcript_json(path: str | Path) -> list[dict]:
    """逐字稿 JSON → [{start, end, text}]。兼容 {segments:[...]} 与 [...] 两种顶层。"""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("segments") or data.get("clips") or []
    out = []
    for seg in data:
        try:
            out.append({
                "start": float(seg["start"]),
                "end": float(seg["end"]),
                "text": str(seg.get("text", "")).strip(),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _track(tracks: list[EditorTrack], tid: str, ttype: str) -> EditorTrack:
    for t in tracks:
        if t.id == tid:
            return t
    t = EditorTrack(id=tid, type=ttype)
    tracks.append(t)
    return t


def sheet_to_project(sheet: dict, root: Path) -> EditorProject:
    """决策单 → EditorProject。root 为项目根（用于解析相对路径）。"""
    width = int(sheet.get("width") or 1080)
    height = int(sheet.get("height") or 1920)
    fps = float(sheet.get("fps") or 30)
    tracks: list[EditorTrack] = []
    total = 0.0

    def resolve(p: str | None) -> str | None:
        if not p:
            return None
        pp = Path(p)
        return str(pp if pp.is_absolute() else (root / pp))

    # ---- 视频轨 ----
    vt = _track(tracks, "video", "video")
    cursor = 0.0
    for i, c in enumerate(sheet.get("clips") or [], 1):
        start = float(c.get("start", 0))
        end = float(c.get("end", 0))
        dur = end - start
        speed = float(c.get("speed", 1.0))
        if not all(math.isfinite(x) for x in (start, end, speed)) or start < 0 or dur <= 0 or speed <= 0:
            raise ValueError(f"clips[{i}]: invalid source interval or speed")
        target_duration = dur / speed
        vt.segments.append(EditorSegment(
            id=f"clip_{i:03d}", track_type="video", t=round(cursor, 6),
            length=round(target_duration, 6), material=resolve(c.get("file")),
            src_in=round(start, 6), src_len=round(dur, 6),
            speed=speed, volume=float(c.get("volume", 1.0)),
            extra=deepcopy({k: v for k, v in c.items()
                            if k not in {"file", "start", "end", "speed", "volume"}}),
        ))
        cursor += target_duration
    total = cursor

    # ---- BGM 轨 ----
    bgm = sheet.get("bgm")
    if isinstance(bgm, dict) and bgm.get("file"):
        bgm_dur = float(bgm.get("duration") or 0.0) or total
        _track(tracks, "bgm", "audio").segments.append(EditorSegment(
            id="bgm_001", track_type="audio", t=0.0,
            length=round(min(bgm_dur, total) or bgm_dur, 6),
            material=resolve(bgm.get("file")),
            volume=float(bgm.get("volume", 0.15)),
        ))

    # ---- 旁白配音轨 ----
    vo = sheet.get("voiceover")
    if isinstance(vo, dict) and vo.get("file"):
        vo_dur = float(vo.get("duration") or 0.0)
        if vo_dur > 0:
            _track(tracks, "voiceover", "audio").segments.append(EditorSegment(
                id="vo_001", track_type="audio", t=0.0, length=round(vo_dur, 6),
                material=resolve(vo.get("file")),
                volume=float(vo.get("volume", 1.0)),
            ))

    # ---- 字幕轨 ----
    subs: list[dict] = []
    tj = sheet.get("subtitles_json")
    if tj and Path(resolve(tj) or "").exists():
        subs = parse_transcript_json(resolve(tj))
    else:
        srt = sheet.get("subtitles")
        if srt and Path(resolve(srt) or "").exists():
            subs = parse_srt(resolve(srt))
    if subs:
        st = _track(tracks, "subtitle", "text")
        for i, s in enumerate(subs, 1):
            start, end = float(s["start"]), min(float(s["end"]), total)
            if start >= total or end - start < 0.05:
                continue
            st.segments.append(EditorSegment(
                id=f"sub_{i:03d}", track_type="text",
                t=round(start, 6), length=round(end - start, 6),
                text=str(s.get("text", "")),
                extra={"style_preset": sheet.get("style"),
                       "subtitle_layout": deepcopy(sheet.get("subtitle_layout") or {})},
            ))

    # ---- 封面承诺 + CTA ----
    cover = sheet.get("cover_text")
    if cover:
        _track(tracks, "cover", "text").segments.append(EditorSegment(
            id="cover_001", track_type="text", t=0.0,
            length=round(min(2.5, total) or 2.5, 6),
            text=str(cover),
        ))
    cta = sheet.get("cta")
    if cta and total > 3:
        _track(tracks, "cta", "text").segments.append(EditorSegment(
            id="cta_001", track_type="text",
            t=round(max(total - 3.0, 0), 6), length=3.0,
            text=str(cta),
        ))

    return EditorProject(
        name=str(sheet.get("project") or "aicut-project"),
        tracks=tracks, duration=round(total, 6), fps=fps,
        width=width, height=height,
        extra={"cta": cta, "template": sheet.get("template"), "style": sheet.get("style"),
               "source_sheet": deepcopy(sheet), "media_root": str(root.resolve()),
               "subtitle_timebase": sheet.get("subtitle_timebase", "edited")},
    )
