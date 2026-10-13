#!/usr/bin/env python3
r"""媒体信号识别：镜头切点、停顿、响度、画面亮度与清晰度。

这一层全部用 ffmpeg 的确定性分析，时间码精确到帧，不依赖模型判断。
输出 JSON 供剪辑决策使用，也可单独查看。

用法:
    python analyze_media.py <视频> [--scene-threshold 0.3] [--silence-db -30] [--min-silence 0.35] [--out work/analysis.json]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIME_RE = re.compile(r"pts_time:([0-9.]+)")
YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")
BLUR_RE = re.compile(r"lavfi\.blurdetect\.blur=([0-9.]+)")


def run_ffmpeg(args: list[str]) -> str:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats"] + args,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (proc.stderr or "") + (proc.stdout or "")


def duration_of(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout
    return float(out.strip() or 0)


def audio_streams(path: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                          "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout
    return len([x for x in out.strip().splitlines() if x.strip()])


def detect_scenes(path: Path, threshold: float) -> list[float]:
    """镜头切点（秒）：画面变化超过阈值的时刻。"""
    log = run_ffmpeg(["-i", str(path), "-filter:v", f"select='gt(scene,{threshold})',showinfo",
                      "-an", "-f", "null", "-"])
    cuts = sorted({round(float(m.group(1)), 3) for m in TIME_RE.finditer(log)})
    return [c for c in cuts if c > 0.05]


def detect_silences(path: Path, db: float, min_len: float) -> list[list[float]]:
    """停顿区间（秒）：持续超过 min_len 的静音。"""
    if not audio_streams(path):
        return []
    log = run_ffmpeg(["-i", str(path), "-af", f"silencedetect=noise={db}dB:d={min_len}",
                      "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[0-9.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", log)]
    spans = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is not None and e > s:
            spans.append([round(max(s, 0.0), 3), round(e, 3)])
    return spans


def loudness(path: Path) -> dict:
    if not audio_streams(path):
        return {}
    log = run_ffmpeg(["-i", str(path), "-af", "ebur128", "-f", "null", "-"])
    lufs = [float(x) for x in re.findall(r"I:\s*(-?[0-9.]+)\s*LUFS", log)]
    peak = [float(x) for x in re.findall(r"Peak:\s*(-?[0-9.]+)\s*dBFS", log)]
    return {"整合响度_LUFS": round(lufs[-1], 2) if lufs else None,
            "真峰值_dBFS": round(max(peak), 2) if peak else None}


def frame_stats(path: Path) -> list[dict]:
    """逐帧亮度与模糊度，用于判断画面质量。"""
    log = run_ffmpeg(["-i", str(path), "-vf", "signalstats,blurdetect,metadata=print",
                      "-an", "-f", "null", "-"])
    frames, cur = [], {}
    for line in log.splitlines():
        t = TIME_RE.search(line)
        if t:
            if cur:
                frames.append(cur)
            cur = {"t": round(float(t.group(1)), 3)}
            continue
        y = YAVG_RE.search(line)
        b = BLUR_RE.search(line)
        if y:
            cur["YAVG"] = round(float(y.group(1)), 2)
        if b:
            cur["blur"] = round(float(b.group(1)), 3)
    if cur:
        frames.append(cur)
    return [f for f in frames if "YAVG" in f or "blur" in f]


def avg(seq: list[float]) -> float | None:
    return round(sum(seq) / len(seq), 3) if seq else None


def main() -> int:
    ap = argparse.ArgumentParser(description="媒体信号识别")
    ap.add_argument("video")
    ap.add_argument("--scene-threshold", type=float, default=0.3)
    ap.add_argument("--silence-db", type=float, default=-30.0)
    ap.add_argument("--min-silence", type=float, default=0.35)
    ap.add_argument("--out", default="work/analysis.json")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.is_absolute():
        video = ROOT / video
    if not video.exists():
        print(f"找不到视频: {video}", file=sys.stderr)
        return 1

    total = duration_of(video)
    cuts = detect_scenes(video, args.scene_threshold)
    silences = detect_silences(video, args.silence_db, args.min_silence)
    loud = loudness(video)
    frames = frame_stats(video)

    # 按切点分段统计画面质量
    bounds = [0.0] + cuts + [total]
    scenes = []
    for i in range(len(bounds) - 1):
        s, e = bounds[i], bounds[i + 1]
        seg = [f for f in frames if s <= f["t"] < e]
        sharp = [f for f in seg if "blur" in f]
        best = min(sharp, key=lambda f: f["blur"])["t"] if sharp else round((s + e) / 2, 2)
        scenes.append({
            "start": round(s, 2), "end": round(e, 2), "duration": round(e - s, 2),
            "亮度均值": avg([f["YAVG"] for f in seg if "YAVG" in f]),
            "模糊度均值": avg([f["blur"] for f in seg if "blur" in f]),
            "最清晰帧_秒": round(float(best), 2),
        })
    blurry = [s for s in scenes if s["模糊度均值"] is not None and s["模糊度均值"] > 0.35]

    result = {
        "video": str(video), "duration": round(total, 3),
        "镜头切点_秒": cuts,
        "镜头数": len(scenes),
        "停顿区间_秒": silences,
        "停顿总时长": round(sum(e - s for s, e in silences), 2),
        "响度": loud,
        "场景": scenes,
        "可能画质不佳的片段": [{"start": s["start"], "end": s["end"], "模糊度": s["模糊度均值"]} for s in blurry],
    }
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"时长 {result['duration']}s | 识别到 {len(cuts)} 个切点、{len(scenes)} 个镜头")
    if cuts:
        print("  切点(秒): " + ", ".join(f"{c:.2f}" for c in cuts[:20]) + (" ..." if len(cuts) > 20 else ""))
    print(f"停顿 {len(silences)} 处，共 {result['停顿总时长']}s" +
          ("：" + ", ".join(f"{s:.2f}-{e:.2f}" for s, e in silences[:8]) if silences else ""))
    if loud:
        print(f"响度 {loud.get('整合响度_LUFS')} LUFS，峰值 {loud.get('真峰值_dBFS')} dBFS")
    if blurry:
        print("  画质提醒: " + ", ".join(f"{s['start']:.1f}-{s['end']:.1f}s(模糊{s['模糊度均值']})" for s in blurry[:5]))
    print(f"结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
