r"""IR 差异：把"这次操作到底干了什么"变成结构化信号。

验证环靠它判断操作是否按预期生效；学习环靠它把"你做的动作"和"结果"对上。
"""
from __future__ import annotations

from .draft import Draft

FIELDS = ("t", "len", "src_in", "src_len", "speed", "text")


def _seg_map(draft: Draft) -> dict:
    return {s.id: s for s in draft.all_segments()}


def _field(seg, name):
    if name == "t":
        return seg.t
    if name == "len":
        return seg.length
    if name == "src_in":
        return seg.src_in
    if name == "src_len":
        return seg.src_len
    if name == "speed":
        return seg.speed
    if name == "text":
        return seg.text
    return None


def diff(a: Draft, b: Draft) -> dict:
    before, after = _seg_map(a), _seg_map(b)
    added = [sid for sid in after if sid not in before]
    removed = [sid for sid in before if sid not in after]
    changed: list[dict] = []
    shifts: list[float] = []

    for sid, seg_b in after.items():
        seg_a = before.get(sid)
        if seg_a is None:
            continue
        for name in FIELDS:
            va, vb = _field(seg_a, name), _field(seg_b, name)
            if va is None and vb is None:
                continue
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                if abs(float(va) - float(vb)) < 1e-4:
                    continue
            elif va == vb:
                continue
            changed.append(
                {
                    "id": sid,
                    "track": seg_b.track_type,
                    "field": name,
                    "before": va,
                    "after": vb,
                    "delta": round(float(vb) - float(va), 6) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None,
                }
            )
        if abs(seg_a.length - seg_b.length) < 1e-4:
            shifts.append(round(seg_b.t - seg_a.t, 6))

    uniform = None
    if shifts and all(abs(s - shifts[0]) < 1e-3 for s in shifts) and abs(shifts[0]) > 1e-3:
        uniform = {"count": len(shifts), "delta": shifts[0]}

    tracks_before = {t.type: len(t.segments) for t in a.tracks}
    tracks_after = {t.type: len(t.segments) for t in b.tracks}
    texts_before = {s.text for s in a.text_segments if s.text}
    texts_after = {s.text for s in b.text_segments if s.text}

    result = {
        "duration": {
            "before": a.declared_duration,
            "after": b.declared_duration,
            "delta": round(b.declared_duration - a.declared_duration, 6),
        },
        "tracks": {"before": tracks_before, "after": tracks_after},
        "added": added,
        "removed": removed,
        "changed": changed,
        "uniform_shift": uniform,
        "texts_added": sorted(texts_after - texts_before),
        "texts_removed": sorted(texts_before - texts_after),
    }
    result["summary"] = summarize(result)
    return result

def summarize(d: dict) -> list[str]:
    lines = [
        f"时长 {d['duration']['before']:.2f}s -> {d['duration']['after']:.2f}s "
        f"（{d['duration']['delta']:+.3f}s）"
    ]
    u = d.get("uniform_shift")
    if u:
        lines.append(f"{u['count']} 个未变形片段整体位移 {u['delta']:+.3f}s")
    if d["added"]:
        lines.append(f"新增 {len(d['added'])} 段")
    if d["removed"]:
        lines.append(f"删除 {len(d['removed'])} 段")
    trimmed = [c for c in d["changed"] if c["field"] == "len"]
    if trimmed:
        lines.append(f"{len(trimmed)} 段长度变化")
    moved = [c for c in d["changed"] if c["field"] == "t"]
    if moved and not u:
        deltas = sorted({round(c["delta"], 3) for c in moved if c.get("delta") is not None})
        head = "、".join(f"{x:+.3f}s" for x in deltas[:3])
        lines.append(f"{len(moved)} 段起点变化（{head}）")
    other = sorted({c["field"] for c in d["changed"] if c["field"] not in ("t", "len")})
    if other:
        lines.append("另有字段变化：" + "、".join(other))
    if d["texts_added"]:
        lines.append("新增字幕：" + " / ".join(t[:16] for t in d["texts_added"][:4]))
    if d["texts_removed"]:
        lines.append("去掉字幕：" + " / ".join(t[:16] for t in d["texts_removed"][:4]))
    if d["tracks"]["before"] != d["tracks"]["after"]:
        lines.append(f"轨道段数 {d['tracks']['before']} -> {d['tracks']['after']}")
    if len(lines) == 1:
        lines.append("没有任何变化")
    return lines
