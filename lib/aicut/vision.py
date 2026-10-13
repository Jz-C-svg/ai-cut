r"""素材视觉事件流（M2）。

为什么这么设计：8GB 显存跑不动逐帧大模型，所以分两档——
  便宜档  全片 2fps 抽帧，用 numpy+PIL 算清晰度/曝光/运动/饱和度/肤色占比，
          几十秒素材几秒内出结果，覆盖全片；
  昂贵档  只有需要时才把"该镜头最清晰的一帧"送视觉模型（见 understand.py 的做法）。

产出是"剪辑能直接用的画面信号"，不是描述：
  糊 / 过曝的区间  -> 该句换 B-roll 或缩短停留
  运动量大的区间   -> 允许更长镜头
  人脸清晰且居中   -> 优先做钩子与落点
  画面里的字幕安全区 -> 字幕避让

所有阈值都按素材自身分布（分位数）判定，不用绝对阈值硬套不同曝光的素材。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from . import perception as _perception
from .memory import open_memory
from .paths import WORK, ffmpeg, ffprobe, fingerprint

DEFAULT_FPS = 2.0
FRAME_WIDTH = 480


def duration_of(path: str | Path) -> float:
    out = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    ).stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def extract_frames(video: str | Path, out_dir: Path, fps: float = DEFAULT_FPS,
                   width: int = FRAME_WIDTH, max_frames: int | None = None) -> list[Path]:
    """一次 ffmpeg 进程抽完，不逐帧起进程。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg(), "-hide_banner", "-nostats", "-loglevel", "error", "-y",
        "-i", str(video),
        "-vf", f"fps={fps},scale={width}:-2",
        "-q:v", "3",
        str(out_dir / "%05d.jpg"),
    ]
    if max_frames is not None:
        cmd[-1:-1] = ['-frames:v', str(max_frames)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise RuntimeError(f'Frame extraction failed: {proc.stderr[-2000:]}')
    return sorted(out_dir.glob("*.jpg"))


def detect_cuts(video: str | Path, threshold: float = 0.3) -> list[float]:
    """镜头切点：画面变化超过阈值的位置（ffmpeg 帧级，比 2fps 抽样准）。"""
    import re

    cmd = [
        ffmpeg(), "-hide_banner", "-nostats",
        "-i", str(video),
        "-filter:v", f"select='gt(scene,{threshold})',showinfo",
        "-an", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    log = (proc.stderr or "") + (proc.stdout or "")
    times = {round(float(m.group(1)), 3) for m in re.finditer(r"pts_time:([0-9.]+)", log)}
    return sorted(t for t in times if t > 0.05)


def _colorfulness(rgb: np.ndarray) -> float:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    rg = np.abs(r - g)
    yb = np.abs(0.5 * (r + g) - b)
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def frame_metrics(img_path: Path, prev_gray: np.ndarray | None = None) -> tuple[dict, np.ndarray]:
    img = Image.open(img_path).convert("RGB")
    rgb = np.asarray(img, dtype=np.float32)
    gray = np.asarray(img.convert("L"), dtype=np.float32)

    lap = (-4.0 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1]
           + gray[1:-1, :-2] + gray[1:-1, 2:])
    blur = float(lap.var())
    motion = float(np.abs(gray - prev_gray).mean()) if prev_gray is not None and prev_gray.shape == gray.shape else 0.0

    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    sat = float(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0).mean())

    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    skin_mask = ((r > 95) & (g > 40) & (b > 20) & (mx - mn > 15)
                 & (np.abs(r - g) > 15) & (r > g) & (r > b))
    skin = float(skin_mask.mean())

    h, w = gray.shape
    center = gray[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    center_lap = (-4.0 * center[1:-1, 1:-1] + center[:-2, 1:-1] + center[2:, 1:-1]
                  + center[1:-1, :-2] + center[1:-1, 2:])

    return ({
        "blur": round(blur, 2),
        "center_blur": round(float(center_lap.var()), 2),
        "bright": round(float(gray.mean()), 1),
        "dark": round(float((gray < 10).mean()), 4),
        "over": round(float((gray > 246).mean()), 4),
        "motion": round(motion, 2),
        "sat": round(sat, 4),
        "color": round(_colorfulness(rgb), 1),
        "skin": round(skin, 4),
    }, gray)


def analyze(video: str | Path, *, fps: float = DEFAULT_FPS, use_cache: bool = True,
            keep_frames: bool = False) -> dict:
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(video)
    fp = fingerprint(video)
    cache_id = f'vision-v2:{fp}:fps={fps}:width={FRAME_WIDTH}'
    mem = open_memory()
    if use_cache:
        cached = mem.get_visual(cache_id)
        if cached:
            cached["cached"] = True
            return cached

    tmp = Path(tempfile.mkdtemp(prefix="aicut-vision-"))
    try:
        frames = extract_frames(video, tmp, fps=fps)
        if not frames:
            raise RuntimeError(f"抽帧失败：{video}")
        rows: list[dict] = []
        prev = None
        for i, fp_img in enumerate(frames):
            m, prev = frame_metrics(fp_img, prev)
            m["t"] = round(i / fps, 3)
            m["i"] = i
            rows.append(m)
        duration = duration_of(video) or round(rows[-1]["t"] + 1.0 / fps, 3)
        cuts = detect_cuts(video, float(_perception.get("scene_cut")))
    finally:
        if not keep_frames:
            shutil.rmtree(tmp, ignore_errors=True)

    shots = _group_shots(rows, cuts, duration)
    result = {
        "video": str(video),
        "fingerprint": fp,
        "fps": fps,
        "duration": round(duration, 3),
        "frames": rows,
        "cuts": cuts,
        "shots": shots,
        "summary": _summarize(shots),
        "cached": False,
    }
    mem.cache_visual(cache_id, str(video), result)
    return result


def _percentiles(rows: list[dict], key: str) -> dict:
    if not rows:
        return {"p20": 0.0, "p50": 0.0, "p80": 0.0}
    vals = np.array([r[key] for r in rows], dtype=np.float32)
    return {"p20": float(np.percentile(vals, 20)), "p50": float(np.percentile(vals, 50)),
            "p80": float(np.percentile(vals, 80))}


def _group_shots(rows: list[dict], cuts: list[float], duration: float) -> list[dict]:
    bounds = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    stat = {k: _percentiles(rows, k) for k in ("blur", "center_blur", "motion", "skin", "bright")}
    shots: list[dict] = []
    for i in range(len(bounds) - 1):
        t0, t1 = bounds[i], bounds[i + 1]
        group = [r for r in rows if t0 - 1e-6 <= r["t"] < t1 - 1e-6] or \
                [r for r in rows if abs(r["t"] - t0) < 1.0]
        if not group:
            continue
        sharpest = max(group, key=lambda r: r["blur"])
        facey = max(group, key=lambda r: r["skin"] * (r["center_blur"] ** 0.5))
        avg = lambda k: round(float(np.mean([r[k] for r in group])), 2)  # noqa: E731
        worst_over = round(max(r["over"] for r in group), 4)
        worst_dark = round(max(r["dark"] for r in group), 4)
        blur_low = max(stat["center_blur"]["p20"] * float(_perception.get("blur_sensitivity")),
                       float(_perception.get("blur_abs_min")))
        blur_high = max(stat["center_blur"]["p80"] * float(_perception.get("blur_sensitivity")),
                        float(_perception.get("blur_abs_good")))
        if avg("center_blur") <= blur_low:
            grade = "糊"
        elif avg("center_blur") >= blur_high:
            grade = "清晰"
        else:
            grade = "一般"
        exposure = "过曝" if worst_over > float(_perception.get("over_ratio")) else ("欠曝" if worst_dark > 0.2 else "正常")
        shots.append({
            "i": i,
            "t0": round(t0, 3),
            "t1": round(t1, 3),
            "len": round(t1 - t0, 3),
            "frames": len(group),
            "blur": avg("blur"),
            "center_blur": avg("center_blur"),
            "motion": avg("motion"),
            "skin": avg("skin"),
            "bright": avg("bright"),
            "grade": grade,
            "exposure": exposure,
            "best_t": sharpest["t"],
            "best_blur": sharpest["blur"],
            "face_t": facey["t"],
            "face_score": round(facey["skin"] * (facey["center_blur"] ** 0.5), 2),
        })
    return shots


def _summarize(shots: list[dict]) -> dict:
    blurry = [s["i"] for s in shots if s["grade"] == "糊"]
    over = [s["i"] for s in shots if s["exposure"] == "过曝"]
    dark = [s["i"] for s in shots if s["exposure"] == "欠曝"]
    lively = [s["i"] for s in shots if s["motion"] >= float(_perception.get("motion_lively"))]
    facey = max(shots, key=lambda s: s["face_score"], default=None)
    hints = []
    if blurry:
        hints.append(f"镜头 {blurry} 偏糊：讲这几句时优先换画面或缩短停留")
    if over:
        hints.append(f"镜头 {over} 有过曝帧：避免把字幕压在高光区")
    if dark:
        hints.append(f"镜头 {dark} 偏暗：可加提亮滤镜")
    if facey and facey["face_score"] > float(_perception.get("skin_face")):
        hints.append(f"镜头 {facey['i']}（{facey['face_t']:.2f}s）主体最清晰：适合做钩子或落点")
    if lively:
        hints.append(f"镜头 {lively} 运动量大：观众需要时间看清，别切太碎")
    return {
        "shots": len(shots),
        "blurry_shots": blurry,
        "overexposed_shots": over,
        "dark_shots": dark,
        "lively_shots": lively,
        "best_face_shot": facey["i"] if facey else None,
        "best_face_t": facey["face_t"] if facey else None,
        "avg_shot_len": round(float(np.mean([s["len"] for s in shots])), 2) if shots else 0.0,
        "hints": hints,
    }


def load(video: str | Path, **kw) -> dict:
    return analyze(video, **kw)


def shot_at(stream: dict, t: float) -> dict | None:
    for s in stream.get("shots", []):
        if s["t0"] - 1e-6 <= t < s["t1"]:
            return s
    return stream.get("shots", [None])[-1]


def blurry_ranges(stream: dict) -> list[tuple[float, float]]:
    return [(s["t0"], s["t1"]) for s in stream.get("shots", []) if s["grade"] == "糊"]


def best_face_shot(stream: dict) -> dict | None:
    shots = stream.get("shots", [])
    return max(shots, key=lambda s: s["face_score"]) if shots else None


def motion_at(stream: dict, t: float) -> float:
    s = shot_at(stream, t)
    return float(s["motion"]) if s else 0.0


def quality_at(stream: dict, t: float) -> str:
    s = shot_at(stream, t)
    return s["grade"] if s else "未知"


def describe(stream: dict) -> list[str]:
    lines = [
        f"视觉事件流 · {Path(stream['video']).name} · {stream['duration']:.2f}s · "
        f"{len(stream['shots'])} 个镜头 · 采样 {stream['fps']}fps"
        + ("（缓存）" if stream.get("cached") else "")
    ]
    for s in stream["shots"]:
        lines.append(
            f"  #{s['i']} {s['t0']:6.2f}->{s['t1']:6.2f} ({s['len']:5.2f}s) "
            f"清晰度{s['blur']:7.1f} 中心{s['center_blur']:7.1f} 运动{s['motion']:5.1f} "
            f"肤色{s['skin']:.3f} {s['grade']}/{s['exposure']} 最清晰@s{s['best_t']:.2f}"
        )
    for hint in stream["summary"]["hints"]:
        lines.append("  · " + hint)
    return lines
