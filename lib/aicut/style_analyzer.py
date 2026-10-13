r"""视频 -> 风格卡分析模块。

从真实下载的短视频样本里，用**确定性信号（ffmpeg 抽帧/帧差/静音）+ 本地视觉模型
（Ollama vision_json）**量化出可执行的剪辑风格参数，产出与 content-library/styles.json
格式兼容的风格卡。

分析维度（每个视频）：
  1. 镜头时长节奏  scene detection -> 平均/最短/最长镜头时长、分布（ffmpeg 确定性）
  2. 转场方式      切点邻域帧差曲线 -> 硬切 / 溶解渐变 推断
  3. 主配色        多帧采样 + numpy k-means 聚类 -> dominant palette
  4. 字幕样式      抽帧送本地视觉模型 -> 位置/颜色/字号感/描边
  5. 开头钩子结构  前 3-5s 帧送视觉模型 + 静音分析 -> 结果前置/痛点共鸣/数字碾压等

所有中间产物（抽帧图、分析 JSON）落到 work/style-frames/<id>/ 下，便于复核。
本模块不写草稿，只产出"事实"；套用由 skills_style.apply_style_card 完成。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from statistics import mean, median

import numpy as np
from PIL import Image

from . import llm
from .paths import WORK, ffmpeg, ffprobe

FRAME_W = 480  # 分析用抽帧宽度，够算节奏/配色，又不占空间

# 字幕样式视觉分析 prompt（要求纯 JSON）
SUBTITLE_PROMPT = """看这一帧短视频截图。只输出 JSON，不要解释，不要代码块：
{"有字幕": true/false,
 "字幕位置": "顶部/中部/底部",
 "字幕颜色": "白/黄/黑/其他",
 "字幕是否有描边": true/false,
 "描边颜色": "黑/白/无",
 "字幕字号相对大小": "小/中/大",
 "每行约几个字": 0}
如果画面里没有任何字幕，「有字幕」填 false 其余留空。"""

HOOK_PROMPT = """看这是短视频开头第 1 秒的一帧。只输出 JSON，不要解释，不要代码块：
{"画面类型": "人物口播/产品展示/文字卡片/空镜/数据图表/其他",
 "是否一上来就给结论或结果": false,
 "是否一上来就抛痛点/问题": false,
 "是否出现大数字/对比": false,
 "一句话概括这一帧在干什么": "15字内"}
据这一帧判断这段开头更像哪种钩子：结果前置/痛点共鸣/数字碾压/悬念/视觉冲击/无明显钩子。"""


# ---------- 基础工具 ----------

def duration_of(path: Path) -> float:
    out = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def _run_ff(args: list[str]) -> str:
    proc = subprocess.run([ffmpeg(), "-hide_banner", "-nostats"] + args,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (proc.stderr or "") + (proc.stdout or "")


def extract_frames(video: Path, out_dir: Path, fps: float = 1.0, width: int = FRAME_W) -> list[Path]:
    """按 fps 抽帧到 out_dir。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(video), "-vf", f"fps={fps},scale={width}:-2", "-q:v", "3",
           str(out_dir / "f_%04d.jpg")]
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return sorted(out_dir.glob("f_*.jpg"))


def detect_cuts(video: Path, threshold: float = 0.3) -> list[float]:
    """镜头切点（秒）。"""
    log = _run_ff(["-i", str(video), "-filter:v",
                   f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"])
    cuts = {round(float(m.group(1)), 3) for m in re.finditer(r"pts_time:([0-9.]+)", log)}
    return sorted(c for c in cuts if c > 0.05)


def detect_silences(path: Path, db: float = -30.0, min_len: float = 0.4) -> list[list[float]]:
    log = _run_ff(["-i", str(path), "-af", f"silencedetect=noise={db}dB:d={min_len}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[0-9.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", log)]
    spans = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is not None and e > s:
            spans.append([round(max(s, 0.0), 3), round(e, 3)])
    return spans


# ---------- 1. 镜头节奏 ----------

def shot_rhythm(duration: float, cuts: list[float]) -> dict:
    """由切点序列算镜头时长统计。"""
    bounds = [0.0] + list(cuts) + [duration]
    lens = [round(bounds[i + 1] - bounds[i], 3) for i in range(len(bounds) - 1)]
    lens = [l for l in lens if l > 0.05]
    if not lens:
        return {"镜头数": 0, "平均镜头秒": None, "最短镜头秒": None, "最长镜头秒": None,
                "镜头时长分布": []}
    lens_sorted = sorted(lens)
    return {
        "镜头数": len(lens),
        "平均镜头秒": round(mean(lens), 2),
        "中位镜头秒": round(median(lens), 2),
        "最短镜头秒": round(min(lens), 2),
        "最长镜头秒": round(max(lens), 2),
        "镜头时长分布秒": [round(x, 2) for x in lens],
        "p25": lens_sorted[len(lens_sorted) // 4],
        "p75": lens_sorted[3 * len(lens_sorted) // 4],
    }


# ---------- 2. 转场推断 ----------

def infer_transitions(video: Path, cuts: list[float], frames_dir: Path,
                      fps: float = 4.0) -> dict:
    """在切点邻域看帧差曲线：单尖峰=硬切；持续若干帧抬高=溶解/渐变。"""
    if not cuts:
        return {"硬切占比": 1.0, "渐变占比": 0.0, "主导转场": "硬切", "样本": []}
    frames = extract_frames(video, frames_dir / "trans", fps=fps, width=240)
    if len(frames) < 4:
        return {"硬切占比": 1.0, "渐变占比": 0.0, "主导转场": "硬切", "样本": []}
    # 相邻帧灰度差序列
    diffs: list[float] = []
    prev = None
    for p in frames:
        with Image.open(p) as im:
            g = np.asarray(im.convert("L").resize((120, 68)), dtype=np.float32)
        diffs.append(0.0 if prev is None else float(np.mean(np.abs(g - prev))))
        prev = g
    # 每帧时间
    t_per = [i / fps for i in range(len(frames))]
    hard, grad = 0, 0
    samples = []
    for c in cuts:
        # 取切点前后 ±0.5s 窗口内的帧差
        win = [(t, d) for t, d in zip(t_per, diffs) if abs(t - c) <= 0.5]
        if len(win) < 3:
            continue
        ds = [d for _, d in win]
        peak = max(ds)
        # 该窗口内有多少帧差持续偏高（> 峰值的一半）
        sustained = sum(1 for d in ds if d > peak * 0.5)
        # 硬切：峰值集中在 1~2 帧；渐变：峰宽较宽
        kind = "溶解渐变" if sustained >= 3 else "硬切"
        if kind == "硬切":
            hard += 1
        else:
            grad += 1
        samples.append({"切点秒": round(c, 2), "峰差": round(peak, 1), "持续高帧": sustained, "判": kind})
    total = hard + grad
    hard_ratio = round(hard / total, 2) if total else 1.0
    return {
        "硬切占比": hard_ratio,
        "渐变占比": round(1 - hard_ratio, 2),
        "主导转场": "硬切" if hard_ratio >= 0.6 else "溶解渐变为主",
        "样本数": total,
        "样本": samples[:12],
    }


# ---------- 3. 主配色 ----------

def _kmeans(pixels: np.ndarray, k: int = 5, iters: int = 12) -> tuple[np.ndarray, np.ndarray]:
    """轻量 k-means（避免 sklearn 依赖）。pixels: (N,3) float。返回 (centers, counts)。"""
    n = len(pixels)
    if n == 0:
        return np.zeros((k, 3)), np.zeros(k)
    rng = np.random.default_rng(42)
    idx = rng.choice(n, size=min(k, n), replace=False)
    centers = pixels[idx].copy()
    if len(centers) < k:  # 不够就复制补齐
        centers = np.tile(centers, (k // len(centers) + 1, 1))[:k]
    for _ in range(iters):
        d = ((pixels[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels = d.argmin(axis=1)
        new_c = centers.copy()
        for j in range(k):
            if (labels == j).any():
                new_c[j] = pixels[labels == j].mean(axis=0)
        if np.allclose(new_c, centers, atol=1.0):
            break
        centers = new_c
    counts = np.bincount(labels, minlength=k)
    order = counts.argsort()[::-1]
    return centers[order], counts[order]


def dominant_colors(video: Path, frames_dir: Path, n_frames: int = 24) -> dict:
    """均匀抽 n_frames 帧，汇总所有像素做 k-means，得到主配色。"""
    dur = duration_of(video)
    if dur <= 0:
        return {"主配色RGB": [], "主配色HEX": [], "样本帧": 0}
    step = dur / (n_frames + 1)
    pts_dir = frames_dir / "color"
    pts_dir.mkdir(parents=True, exist_ok=True)
    pixels = []
    for i in range(1, n_frames + 1):
        p = pts_dir / f"c_{i:02d}.jpg"
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{i * step:.2f}", "-i", str(video), "-frames:v", "1",
               "-vf", "scale=160:-2", str(p)]
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if not p.exists():
            continue
        with Image.open(p) as im:
            arr = np.asarray(im.convert("RGB"), dtype=np.float32)[::2, ::2]
            pixels.append(arr.reshape(-1, 3))
    if not pixels:
        return {"主配色RGB": [], "主配色HEX": [], "样本帧": 0}
    allp = np.concatenate(pixels, axis=0)
    centers, counts = _kmeans(allp, k=5)
    total = counts.sum() or 1
    hexs, rgbs, weights = [], [], []
    for c, n in zip(centers, counts):
        r, g, b = int(round(c[0])), int(round(c[1])), int(round(c[2]))
        hexs.append("#{:02X}{:02X}{:02X}".format(r, g, b))
        rgbs.append([r, g, b])
        weights.append(round(float(n) / float(total), 2))
    return {"主配色RGB": rgbs, "主配色HEX": hexs, "占比": weights, "样本帧": len(pixels)}


# ---------- 4/5. 视觉模型：字幕样式 + 钩子 ----------

def _vision_safe(frame: Path, prompt: str) -> dict:
    try:
        return llm.vision_json(frame, prompt, provider="ollama")
    except Exception as exc:  # noqa: BLE001
        return {"错误": f"{type(exc).__name__}: {exc}"[:160]}


def analyze_subtitle_style(video: Path, frames_dir: Path) -> dict:
    """抽中段若干帧送视觉模型判字幕样式（取多数票）。"""
    dur = duration_of(video)
    if dur <= 0:
        return {}
    candidates = [dur * f for f in (0.3, 0.45, 0.6)]
    votes: dict = {}
    detail = []
    for t in candidates:
        p = frames_dir / f"sub_{int(t)}.jpg"
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}",
               "-i", str(video), "-frames:v", "1", "-vf", f"scale={FRAME_W}:-2", str(p)]
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if not p.exists():
            continue
        r = _vision_safe(p, SUBTITLE_PROMPT)
        detail.append({"t": round(t, 2), "判": r})
        if r.get("有字幕") is True:
            for k in ("字幕位置", "字幕颜色", "字幕是否有描边", "每行约几个字"):
                v = r.get(k)
                if v is not None:
                    votes.setdefault(k, []).append(v)
    if not votes:
        return {"是否有字幕": False, "说明": "抽样帧未检出字幕（或为无字幕素材）", "帧明细": detail}
    out = {"是否有字幕": True}
    for k, vals in votes.items():
        # 多数票
        out[k] = max(set(vals), key=vals.count)
    out["帧明细"] = detail
    return out


def analyze_hook(video: Path, frames_dir: Path) -> dict:
    """前 3 秒：抽第 0.5s 和第 2.5s 两帧送视觉模型，叠加静音信号判钩子类型。"""
    hooks_dir = frames_dir / "hook"
    frames_dir.mkdir(parents=True, exist_ok=True)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    reads = []
    for t in (0.5, 2.5):
        p = hooks_dir / f"hook_{int(t)}.jpg"
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}",
               "-i", str(video), "-frames:v", "1", "-vf", f"scale={FRAME_W}:-2", str(p)]
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.exists():
            reads.append({"t": t, "判": _vision_safe(p, HOOK_PROMPT)})
    sil = detect_silences(video, -30.0, 0.4)
    head_silence = [s for s in sil if s[0] < 3.0]
    # 综合两帧判钩子类型
    v1 = reads[0]["判"] if reads else {}
    hook_type = "无明显钩子"
    if v1.get("是否一上来就给结论或结果"):
        hook_type = "结果前置"
    elif v1.get("是否一上来就抛痛点/问题"):
        hook_type = "痛点共鸣"
    elif v1.get("是否出现大数字/对比"):
        hook_type = "数字碾压"
    elif v1.get("画面类型") in ("文字卡片", "数据图表"):
        hook_type = "数字碾压"
    elif v1.get("画面类型") in ("空镜", "产品展示"):
        hook_type = "视觉冲击"
    return {
        "钩子类型": hook_type,
        "首帧画面类型": v1.get("画面类型"),
        "开头3秒静音": head_silence,
        "帧明细": reads,
    }


# ---------- 汇总 -> 风格卡 ----------

def _pick_filter_name(rhythm: dict, colors: dict) -> tuple[str, int]:
    """按节奏/亮度粗映射到本机剪映已有滤镜名（与 styles.json 对齐）。"""
    avg = rhythm.get("平均镜头秒") or 3.0
    hexs = colors.get("主配色HEX") or []
    # 简单判冷暖：第一个主色
    warm = False
    if hexs:
        h = hexs[0].lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        warm = r > b
    if avg < 1.5:
        return "氛围感", 45
    if avg > 6:
        return "中性", 20
    return ("去黄提亮" if warm else "清晰明亮"), 35


def analyze(video: str | Path, *, name: str, platform: str, source_url: str,
            progress=print) -> dict:
    """对一个视频样本产出完整风格卡事实。"""
    video = Path(video)
    frames_dir = WORK / "style-frames" / name
    frames_dir.mkdir(parents=True, exist_ok=True)

    progress(f"[风格分析] {video.name} -> {name}")
    dur = duration_of(video)
    progress(f"  时长 {dur:.1f}s")
    cuts = detect_cuts(video, 0.3)
    progress(f"  scene 切点 {len(cuts)}")
    rhythm = shot_rhythm(dur, cuts)
    progress(f"  节奏 平均 {rhythm.get('平均镜头秒')}s 区间 {rhythm.get('最短镜头秒')}-{rhythm.get('最长镜头秒')}")
    trans = infer_transitions(video, cuts, frames_dir)
    progress(f"  转场 {trans.get('主导转场')} 硬切占比 {trans.get('硬切占比')}")
    colors = dominant_colors(video, frames_dir)
    progress(f"  主配色 {colors.get('主配色HEX')}")
    sub = analyze_subtitle_style(video, frames_dir)
    progress(f"  字幕 {'有' if sub.get('是否有字幕') else '无'} {sub.get('字幕位置','')}")
    hook = analyze_hook(video, frames_dir)
    progress(f"  钩子 {hook.get('钩子类型')}")

    # 镜头区间：p25~p75 作为可执行区间，落在经验安全范围
    lo = rhythm.get("p25") or rhythm.get("平均镜头秒") or 3.0
    hi = rhythm.get("p75") or rhythm.get("平均镜头秒") or 5.0
    lo = max(0.4, round(lo, 1))
    hi = max(lo + 0.3, round(hi, 1))
    filter_name, filter_int = _pick_filter_name(rhythm, colors)

    sub_pos = sub.get("字幕位置")
    # 字幕纵向位置：底部 -> -0.78（与现有风格卡一致），中部 -> -0.2，顶部 -> 0.6
    y_map = {"底部": -0.78, "中部": -0.2, "顶部": 0.6}
    sub_y = y_map.get(sub_pos, -0.78) if sub.get("是否有字幕") else -0.78
    per_line = sub.get("每行约几个字") or 14
    stroke = bool(sub.get("字幕是否有描边")) if sub.get("是否有字幕") else True

    # 转场调用名映射
    if trans.get("主导转场", "").startswith("硬切"):
        trans_name, trans_dur = None, 0.3
    else:
        trans_name, trans_dur = "模糊", 0.5

    card = {
        "id": name,
        "名称": f"分析风格-{name}",
        "来源平台": platform,
        "来源视频": source_url,
        "源文件": str(video),
        "剪辑参数": {
            "text_style_preset": "口播标准",
            "滤镜": filter_name,
            "滤镜强度": filter_int,
            "镜头平均长度": f"{lo:g}-{hi:g} 秒",
            "镜头最短秒": lo,
            "镜头最长秒": hi,
            "转场调用名": trans_name,
            "转场时长秒": trans_dur,
            "主配色": colors.get("主配色HEX", []),
            "字幕": {
                "位置": sub_y,
                "每行字数": str(per_line),
                "颜色": sub.get("字幕颜色", "白"),
                "描边": "有" if stroke else "无",
            },
            "BGM音量": 0.18,
        },
        "结构": "hook-explain-close",
        "钩子类型": [hook.get("钩子类型", "无明显钩子")],
        "分析证据": {
            "时长秒": round(dur, 2),
            "镜头节奏": rhythm,
            "转场推断": trans,
            "主配色": colors,
            "字幕样式": sub,
            "开头钩子": hook,
        },
    }
    out = frames_dir / "analysis.json"
    out.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(f"  分析卡已写 {out}")
    return card
