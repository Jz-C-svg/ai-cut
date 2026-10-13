#!/usr/bin/env python3
r"""音乐节拍分析：给卡点风格提供节拍点（纯标准库实现，不依赖 numpy）。

用法:
    python beats.py --audio <音频或视频> [--out work/beats.json]
    python beats.py --video <成片素材> --use-bgm   # 没有 BGM 时分析素材自身音频
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SR = 22050
WIN = 1024
HOP = 512


def read_pcm(path: Path) -> list[float]:
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
           "-f", "s16le", "-acodec", "pcm_s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    return [b / 32768.0 for b in memoryview(raw).cast("h")]


def envelope(samples: list[float]) -> list[float]:
    env = []
    for i in range(0, max(1, len(samples) - WIN), HOP):
        chunk = samples[i:i + WIN]
        env.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)))
    return env


def detect_beats(env: list[float]) -> tuple[float, list[float]]:
    if len(env) < 8:
        return 0.0, []
    onset = [max(0.0, env[i] - env[i - 1]) for i in range(1, len(env))]
    mean = st.mean(onset)
    std = st.pstdev(onset) or 1e-9
    thr = mean + 1.2 * std
    min_gap = int(0.15 * SR / HOP)
    peaks: list[int] = []
    for i, v in enumerate(onset):
        if v < thr:
            continue
        lo, hi = max(0, i - min_gap), min(len(onset), i + min_gap + 1)
        if v >= max(onset[lo:hi]):
            if not peaks or i - peaks[-1] >= min_gap:
                peaks.append(i)
    beat_times = [round(p * HOP / SR, 3) for p in peaks]

    # 用自相关估计 BPM（60-200）
    best_bpm, best_score = 0.0, 0.0
    for bpm in range(60, 201):
        lag = int(round((60.0 / bpm) * SR / HOP))
        if lag < 1 or lag >= len(onset):
            continue
        score = sum(onset[i] * onset[i - lag] for i in range(lag, len(onset)))
        if score > best_score:
            best_bpm, best_score = float(bpm), score
    # 用节拍间隔的中位数校正倍频错误（自相关常把 120 判成 60）
    if len(beat_times) >= 4:
        gaps = [beat_times[i + 1] - beat_times[i] for i in range(len(beat_times) - 1)]
        gaps = [g for g in gaps if 0.2 <= g <= 2.0]
        if gaps:
            median_gap = st.median(gaps)
            if median_gap > 0:
                best_bpm = 60.0 / median_gap
    return best_bpm, beat_times

def main() -> int:
    ap = argparse.ArgumentParser(description="音乐节拍分析")
    ap.add_argument("--audio", help="音频或视频文件")
    ap.add_argument("--out", default="work/beats.json")
    ap.add_argument("--max-beats", type=int, default=200)
    args = ap.parse_args()

    if not args.audio:
        print("需要 --audio", file=sys.stderr)
        return 2
    src = Path(args.audio)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        print(f"找不到文件: {src}", file=sys.stderr)
        return 1

    samples = read_pcm(src)
    if len(samples) < SR // 2:
        print("音频太短，无法分析", file=sys.stderr)
        return 1
    env = envelope(samples)
    bpm, beats = detect_beats(env)

    result = {"文件": str(src), "时长": round(len(samples) / SR, 2),
              "BPM": round(bpm, 1), "节拍数": len(beats), "节拍": beats[:args.max_beats]}
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[节拍] 估计 BPM {result['BPM']}，识别到 {len(beats)} 个节拍点（时长 {result['时长']}s）")
    if beats:
        print("   前 12 个节拍(秒): " + ", ".join(f"{b:.2f}" for b in beats[:12]))
    print(f"结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
