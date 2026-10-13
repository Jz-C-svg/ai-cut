r"""智能抽帧 + 感知去重（v3.3 P0-2）。

对照 claude-real-video（场景感知 + 去重抽帧）与 screen-lens（多信号关键帧）：
  · 场景感知：复用 vision.detect_cuts（ffmpeg select='gt(scene,0.3)'）拿切点；
    在切点后取代表帧，再按 min_interval 强制补帧（防止长固定镜头无帧可取）。
  · 感知去重：dHash（8x8 灰度相邻差分 -> 64bit），与上一个**保留**帧算汉明距离，
    < 阈值（默认 5）判为重复，跳过。

与 v3.2 提速 B7（shotprof 内 ahash）的关系——两层互补、不重复实现：
  · 本模块是**抽帧层**：减少交给后续流程的帧数；
  · B7 是 **VLM 调用层**：在帧已抽到后、调视觉模型前再判一次重复。
  两层哈希算法不同（这里 dHash 差分，B7 ahash 均值），阈值各自独立。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from .paths import ffmpeg, ffprobe
from . import vision

DEFAULT_HASH_THRESHOLD = 5  # 汉明距离 < 此值判为重复
FRAME_W = 480


# ---------- dHash（差分哈希） ----------

def dhash_file(path: str | Path, size: int = 8) -> str:
    """8x8 灰度图相邻像素差分 -> 64bit 哈希串（'0'/'1'）。"""
    with Image.open(path) as im:
        g = np.asarray(im.convert("L").resize((size + 1, size)), dtype=np.int16)
    # 每行相邻像素：左 < 右 -> 1
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return "".join("1" if b else "0" for b in bits)


def hamming(a: str, b: str) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


# ---------- 候选帧（未去重） ----------

def _probe_duration(video: Path) -> float:
    out = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    ).stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def _extract_one(video: Path, t: float, out: Path) -> bool:
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{max(t, 0):.3f}", "-i", str(video),
           "-frames:v", "1", "-vf", f"scale={FRAME_W}:-2", str(out)]
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out.exists() and out.stat().st_size > 0


def candidate_frames(video: str | Path, *, method: str = "scene",
                     min_interval: float = 1.0, scene_thr: float = 0.3) -> list[dict]:
    """生成候选抽帧（未去重）：切点帧 + 强制补帧。

    Returns:
        [{frame_path, timestamp, scene_score}]，按 timestamp 升序。
    """
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(video)
    duration = _probe_duration(video)
    tmp = Path(tempfile.mkdtemp(prefix="aicut-fx-"))

    cuts: list[float] = []
    if method in ("scene", "both"):
        try:
            cuts = vision.detect_cuts(video, scene_thr)
        except Exception:  # noqa: BLE001
            cuts = []

    # 候选时间戳：切点后 0.1s（拿新镜头第一帧）+ 强制补帧
    times: list[float] = []
    for c in cuts:
        times.append(round(min(c + 0.1, duration), 3))
    if duration > 0:
        t = 0.0
        while t < duration:
            times.append(round(min(t, duration), 3))
            t += max(min_interval, 0.5)
    # 合并去重（时间近似）
    merged: list[float] = []
    for x in sorted(times):
        if not merged or abs(x - merged[-1]) > 0.15:
            merged.append(x)

    out: list[dict] = []
    try:
        for i, t in enumerate(merged):
            p = tmp / f"f_{i:04d}_{t:.2f}.jpg"
            if _extract_one(video, t, p):
                # scene_score：该时间是否落在切点附近
                scene_score = 1.0 if any(abs(t - c) < 0.2 for c in cuts) else 0.0
                out.append({"frame_path": str(p), "timestamp": t,
                            "scene_score": scene_score, "_tmpdir": str(tmp)})
    except Exception:  # noqa: BLE001
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return out


def extract_keyframes(video_path: str | Path, *, method: str = "scene",
                      dedup: bool = True, min_interval: float = 1.0,
                      hash_threshold: int = DEFAULT_HASH_THRESHOLD,
                      keep_temp: bool = False) -> list[dict]:
    r"""场景感知 + 感知去重抽帧。

    Args:
        video_path: 视频文件。
        method: "scene"（场景切换+补帧）/ "fixed"（仅补帧，不做场景检测）。
        dedup: 是否开启感知去重（dHash 汉明距离 < hash_threshold 判重复）。
        min_interval: 强制补帧最小间隔（秒）。
        hash_threshold: dHash 汉明距离阈值。
        keep_temp: 是否保留抽帧临时目录（调试用；默认随返回帧一起由调用方清理）。

    Returns:
        保留下来的关键帧列表 ``[{frame_path, timestamp, hash, scene_score, is_duplicate: False}]``；
        去重掉的帧不计入返回，但计数见每条返回无——调用方可用 candidate_frames 对比前后帧数。
    """
    cands = candidate_frames(video_path, method=("scene" if method == "scene" else "fixed"),
                             min_interval=min_interval)
    kept: list[dict] = []
    last_hash: str | None = None
    for c in cands:
        try:
            h = dhash_file(c["frame_path"])
        except Exception:  # noqa: BLE001
            h = ""
        is_dup = False
        if dedup and last_hash is not None and h and hamming(last_hash, h) < hash_threshold:
            is_dup = True
        frame = {
            "frame_path": c["frame_path"],
            "timestamp": c["timestamp"],
            "hash": h,
            "scene_score": c["scene_score"],
            "is_duplicate": is_dup,
        }
        if is_dup:
            # 重复帧：删文件、不保留
            if not keep_temp:
                Path(c["frame_path"]).unlink(missing_ok=True)
            continue
        kept.append(frame)
        last_hash = h
    return kept


def cleanup(frames: list[dict]) -> None:
    """清理抽帧产生的临时帧文件及其目录。"""
    seen: set[str] = set()
    for fr in frames:
        p = Path(fr["frame_path"])
        d = str(p.parent)
        if d in seen:
            continue
        seen.add(d)
        shutil.rmtree(d, ignore_errors=True)
