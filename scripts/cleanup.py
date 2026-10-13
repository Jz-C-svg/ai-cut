r"""粗剪清理：删掉拍摄口令（如「3 2 1 开始」）并把时间线重排。

规则来自 config/cleanup_rules.json。命中的片段会从时间线上真正剪掉，
后面的字幕和画面整体前移，而不是只隐藏字幕。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT/'lib') not in sys.path:sys.path.insert(0,str(ROOT/'lib'))
RULES_FILE = ROOT / "config" / "cleanup_rules.json"


def load_rules() -> dict:
    cfg = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    cfg["_compiled"] = [re.compile(p) for p in cfg["删除口令"]["规则"]]
    return cfg


def is_filler(text: str, cfg: dict) -> bool:
    from aicut.material_screening import COMMAND
    from aicut.retake import norm
    return bool(COMMAND.fullmatch(norm(text)))


def find_cut_ranges(segments: list[dict], cfg: dict, total: float) -> list[list[float]]:
    """找出需要剪掉的时间区间（秒）。"""
    spec = cfg["删除口令"]
    from aicut.material_screening import screen_transcript
    raw = [[r['start'],r['end']] for r in screen_transcript(segments,total)
           if r['action']=='auto_remove' and r['kind'] in {'production_countdown','production_cue'}]
    if not raw:
        return []
    raw.sort()
    merged: list[list[float]] = []
    for s, e in raw:
        if merged and s - merged[-1][1] <= float(spec["合并间隙"]):
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    # No edge snapping across an earlier/later spoken sentence.
    snap = float(spec["首尾吸附"])
    if merged and merged[0][0] <= snap and not any(float(x['start'])<merged[0][0] for x in segments):merged[0][0]=0.0
    if merged and total-merged[-1][1]<=snap and not any(float(x['end'])>merged[-1][1] for x in segments):merged[-1][1]=total
    return merged


def keep_ranges(cuts: list[list[float]], total: float, cfg: dict) -> list[list[float]]:
    min_keep = float(cfg["删除口令"]["最小保留片段"])
    keeps: list[list[float]] = []
    cursor = 0.0
    for s, e in cuts:
        if s - cursor > .001:
            keeps.append([cursor, s])
        cursor = max(cursor, e)
    if total - cursor > .001:
        keeps.append([cursor, total])
    return keeps


def map_time(t: float, cuts: list[list[float]]) -> float:
    """把原时间线的时间映射到剪后时间线。"""
    shift = 0.0
    for s, e in cuts:
        if t >= e:
            shift += e - s
        elif t > s:
            return max(0.0, s - shift)
    return max(0.0, t - shift)


def drop_hidden(segments: list[dict], cuts: list[list[float]]) -> list[dict]:
    out = []
    for seg in segments:
        s, e = float(seg["start"]), float(seg["end"])
        if any(s >= cs - 0.05 and e <= ce + 0.05 for cs, ce in cuts):
            continue
        out.append(dict(seg))
    return out

def clean_transcript_file(transcript: Path, total: float | None = None, cfg: dict | None = None) -> dict:
    """按规则清洗逐字稿，写出 <名称>.clean.json/.srt/.txt。"""
    cfg = cfg or load_rules()
    data = json.loads(transcript.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    total = total or (max((float(s["end"]) for s in segments), default=0.0))
    cuts = find_cut_ranges(segments, cfg, total)
    kept = drop_hidden(segments, cuts)
    for seg in kept:
        seg["start"] = round(map_time(float(seg["start"]), cuts), 2)
        seg["end"] = round(map_time(float(seg["end"]), cuts), 2)
    data["segments"] = kept
    data["duration"] = round(total - sum(e - s for s, e in cuts), 2)
    data["cleanup_cuts"] = [[round(s, 2), round(e, 2)] for s, e in cuts]

    base = transcript.with_suffix("")
    (base.parent / (base.name + ".clean.json")).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (base.parent / (base.name + ".clean.txt")).write_text(
        "\n".join(s["text"] for s in kept), encoding="utf-8")
    lines = []
    for i, seg in enumerate(kept, 1):
        def fmt(t):
            ms = int(round(t * 1000))
            h, ms = divmod(ms, 3600000)
            m, ms = divmod(ms, 60000)
            s, ms = divmod(ms, 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        lines.append(f"{i}\n{fmt(seg['start'])} --> {fmt(seg['end'])}\n{seg['text']}\n")
    (base.parent / (base.name + ".clean.srt")).write_text("\n".join(lines), encoding="utf-8")
    return {"cuts": cuts, "kept": len(kept), "duration": data["duration"]}


if __name__ == "__main__":
    import sys as _sys
    for arg in _sys.argv[1:]:
        info = clean_transcript_file(Path(arg))
        cuts = ", ".join(f"{s:.2f}-{e:.2f}s" for s, e in info["cuts"]) or "无"
        print(f"{Path(arg).name}: 剪除 {cuts} | 保留 {info['kept']} 句 | 时长 {info['duration']}s")

def clean_to_source(t: float, keeps: list[list[float]], *, endpoint='start') -> float:
    if endpoint not in {'start','end'}:raise ValueError('endpoint must be start or end')
    acc = 0.0
    for i,(s, e) in enumerate(keeps):
        span = e - s
        if t < acc+span-1e-8 or (abs(t-acc-span)<=1e-8 and (endpoint=='end' or i==len(keeps)-1)):
            return s + (t - acc)
        acc += span
    return keeps[-1][1] if keeps else t

def merge_ranges(ranges: list[list[float]], gap: float = 0.05) -> list[list[float]]:
    out: list[list[float]] = []
    for s, e in sorted(ranges):
        if e - s <= 0:
            continue
        if out and s - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out
