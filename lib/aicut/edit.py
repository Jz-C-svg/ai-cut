r"""草稿编辑原语（工程通道）。

这些函数直接改 `draft_content.json` 里的片段，不点界面。好处是精确、可 diff、
可回滚；界面通道只用于工程文件做不到的事（花字、云字体、导出等）。

时间对外一律用秒，内部换算成微秒运算，避免浮点误差累积。

dry_run 的语义：**照常改内存里的草稿**（这样报告、diff、验证都是真实结果），
只是由调用方决定不落盘。这样"演算"与"真做"走的是同一条代码路径。
"""
from __future__ import annotations

import copy

from .draft import Draft, Segment, new_id, to_us


def _rng(seg: Segment) -> tuple[int, int]:
    r = seg.raw.get("target_timerange") or {}
    return int(r.get("start", 0)), int(r.get("duration", 0))


def _set_rng(seg: Segment, start_us: int, dur_us: int) -> None:
    seg.raw["target_timerange"] = {"start": max(int(start_us), 0), "duration": max(int(dur_us), 0)}


def _src(seg: Segment) -> tuple[int, int] | None:
    r = seg.raw.get("source_timerange")
    if not r:
        return None
    return int(r.get("start", 0)), int(r.get("duration", 0))


def _set_src(seg: Segment, start_us: int, dur_us: int) -> None:
    if seg.raw.get("source_timerange"):
        seg.raw["source_timerange"] = {"start": max(int(start_us), 0), "duration": max(int(dur_us), 0)}


def _filter_keyframes(seg: Segment, drop_from: int, drop_to: int, shift: int) -> None:
    """关键帧跟片段一起动：丢掉落在被删区间里的，其余按时长偏移。"""
    for group in seg.raw.get("common_keyframes") or []:
        times = group.get("keyframe_list")
        if not isinstance(times, list):
            continue
        kept = []
        for kf in times:
            off = int(kf.get("time_offset", 0))
            if drop_from <= off < drop_to:
                continue
            if shift and off >= drop_to:
                kf = {**kf, "time_offset": off - shift}
            kept.append(kf)
        group["keyframe_list"] = kept


def ripple_delete(draft: Draft, t0: float, t1: float, *, dry_run: bool = False) -> dict:
    """删除时间线 [t0, t1)，后面的内容连同画面/字幕/特效整体前移。"""
    if t1 <= t0:
        raise ValueError("t1 必须大于 t0")
    s0, s1 = to_us(t0), to_us(t1)
    gap = s1 - s0
    HARD_END = 1 << 62

    report: dict = {
        "op": "ripple_delete",
        "range": [round(t0, 6), round(t1, 6)],
        "duration_before": round(draft.declared_duration, 6),
        "deleted": [],
        "trimmed": [],
        "split": [],
        "shifted": 0,
        "dry_run": dry_run,
    }

    for track in draft.tracks:
        kept_raw: list[dict] = []
        for seg in track.segments:
            start, dur = _rng(seg)
            end = start + dur
            src = _src(seg)
            speed = seg.speed or 1.0
            action = "keep"

            if end <= s0:
                action = "keep"  # 完全在删除点之前，原样保留
            elif start >= s1:
                _set_rng(seg, start - gap, dur)
                action = "shifted"
                report["shifted"] += 1
            elif start < s0 and end > s1:
                # 跨越被删区间：左段原样 + 右段前移到 s0
                left_dur = s0 - start
                right_dur = end - s1
                right = copy.deepcopy(seg.raw)
                right["id"] = new_id()
                right_seg = Segment(raw=right, track_type=track.type)
                _filter_keyframes(seg, left_dur, HARD_END, 0)
                _filter_keyframes(right_seg, 0, s1 - start, s1 - start)
                _set_rng(seg, start, left_dur)
                if src:
                    _set_src(seg, src[0], left_dur * speed)
                _set_rng(right_seg, s0, right_dur)
                if src:
                    right["source_timerange"] = {
                        "start": src[0] + int(round((s1 - start) * speed)),
                        "duration": int(round(right_dur * speed)),
                    }
                kept_raw.append(right)
                report["split"].append(seg.id)
                action = "trimmed"
            elif start < s0 and end <= s1:
                _set_rng(seg, start, s0 - start)
                if src:
                    _set_src(seg, src[0], (s0 - start) * speed)
                action = "trimmed"
            elif start >= s0 and end > s1:
                new_dur = end - s1
                head_cut = s1 - start
                if src:
                    _set_src(seg, src[0] + int(round(head_cut * speed)), int(round(new_dur * speed)))
                _set_rng(seg, s0, new_dur)
                _filter_keyframes(seg, 0, head_cut, head_cut)
                action = "trimmed"
            else:
                action = "deleted"

            if action == "deleted":
                report["deleted"].append(seg.id)
            else:
                if action == "trimmed":
                    report["trimmed"].append(seg.id)
                kept_raw.append(seg.raw)

        track.raw["segments"] = kept_raw

    draft._tracks = None
    draft.content["duration"] = to_us(draft.max_end)
    report["duration_after"] = round(draft.declared_duration, 6)
    return report


def set_speed(seg: Segment, speed: float) -> None:
    seg.raw["speed"] = float(speed)


def set_volume(seg: Segment, volume: float) -> None:
    seg.raw["volume"] = float(volume)


def move_segment(seg: Segment, new_start: float) -> None:
    _set_rng(seg, to_us(new_start), to_us(seg.length))


def shift_all(draft: Draft, delta: float) -> None:
    delta_us = to_us(delta)
    for seg in draft.all_segments():
        start, dur = _rng(seg)
        _set_rng(seg, start + delta_us, dur)
    draft._tracks = None
    draft.content["duration"] = to_us(draft.max_end)
