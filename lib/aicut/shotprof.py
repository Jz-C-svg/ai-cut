r"""镜头视觉档案：把"画面"变成可决策、可验证的结构化事实。

和 vision.py 的分工：
  vision.py   便宜档：全片 2fps 指标（清晰度/曝光/运动/肤色）——覆盖全片，几乎零成本
  shotprof.py 精细档：每个镜头挑最有代表性的几帧，本地算"主体框/安全区/抖动/闪烁/水印嫌疑/
              重复镜头"，再把最清晰那一帧交给视觉模型读出**语义**（主体/动作/景别/构图/光线/
              可辨认度/适不适合当钩子/字幕会不会挡脸/美学分）

这两层合起来才是"看得懂画面"：指标告诉你"清不清楚"，档案告诉你"是什么、能不能用"。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import llm, vision
from .memory import open_memory
from .paths import WORK, ffmpeg, fingerprint

FRAME_W = 960

# ---------- v3.2 提速开关（可由环境变量覆盖，便于逐项 A/B 实测） ----------
# B8：镜头 VLM 并发数（配合 Ollama 服务端 OLLAMA_NUM_PARALLEL=2；服务需重启才生效）
SHOTPROF_WORKERS = int(os.environ.get("SHOTPROF_WORKERS", "2"))
# B7：感知哈希预判重复镜头、跳过 VLM（设 0 关闭，便于单独实测 B7 收益）
HAM_SKIP_ENABLED = os.environ.get("SHOTPROF_SKIP_SIMILAR", "1") != "0"
# B7 阈值：64bit 感知哈希汉明距离 < N 判为相似帧
HAM_SIMILAR_DIST = int(os.environ.get("SHOTPROF_HAM_DIST", "5"))

# ---------- v3.9.0：抽帧技巧三件套（全部环境变量控制，默认关闭，不影响现有行为） ----------
# (2) 长镜头密采样：SHOTPROF_DENSE_FRAMES=N（>0 开启）；对 >10s 镜头均匀抽 N 帧，
#     用 _ahash/_hamming 做相邻去重。仅做帧采样与本地记录，不改变 VLM 调用次数（仍取 best_frame）。
DENSE_FRAMES = int(os.environ.get("SHOTPROF_DENSE_FRAMES", "0"))
DENSE_MIN_LEN = float(os.environ.get("SHOTPROF_DENSE_MIN_LEN", "10.0"))
DENSE_HAM = int(os.environ.get("SHOTPROF_DENSE_HAM", "8"))
# (3) 分步提问：SHOTPROF_STEPWISE=1 时 _understand_merged 分两次 VLM 调用
#     （第一步只问画面主体/内容，第二步问三槽位+美学）。默认关闭，沿用 MERGED_PROMPT 一步模式。
STEPWISE_MODE = os.environ.get("SHOTPROF_STEPWISE", "0") == "1"

# ---------- TOP4：三封闭槽位枚举（来源 L-Storyboard 论文 arXiv 2505.12237） ----------
# 视觉模型只能从这些值里选；不在枚举里的输出会被强校验降级为 closest match。
SHOT_SIZES = {
    "extreme_close_up": "特写", "close_up": "近景", "medium": "中景",
    "wide": "全景", "extreme_wide": "远景",
}
SHOT_ANGLES = {
    "high_angle": "俯拍", "low_angle": "仰拍", "eye_level": "平视",
    "overhead": "顶拍", "aerial": "航拍",
}
SHOT_MOTIONS = {
    "locked": "固定", "handheld": "手持", "tilt": "俯仰",
    "zoom": "推拉", "pan": "摇移", "static": "静止",
}
SHOT_EMOTIONS = {"calm", "tense", "warm", "neutral"}
# 中文 -> 英文枚举的兜底映射（复用 _understand 里"景别"字段的中文回答）
CN_SIZE_MAP = {
    "特写": "close_up", "大特写": "extreme_close_up", "极特写": "extreme_close_up",
    "近景": "close_up", "中景": "medium", "全景": "wide", "远景": "extreme_wide",
}
THREE_SLOT_PROMPT = """看这一帧画面。只输出 JSON，不要解释，不要代码块：
{"shot_size": "...", "shot_angle": "...", "shot_motion": "...",
 "emotion_tag": "...", "confidence": 0.0}
其中 shot_size 只能填：extreme_close_up / close_up / medium / wide / extreme_wide
shot_angle 只能填：high_angle / low_angle / eye_level / overhead / aerial
shot_motion 只能填：locked / handheld / tilt / zoom / pan / static
emotion_tag 只能填：calm / tense / warm / neutral（不确定就 neutral）
confidence 是 0.0-1.0 的小数，表示你对这次分类的把握。"""

SUB_BAND = 0.30  # 字幕带取画面下方 30%（剪映默认字幕位置在下方）

SCHEMA_PROMPT = """看这一帧画面。只输出 JSON，不要解释，不要代码块：
{"主体": "画面里最主要的人或物（一句话，20字内）",
 "动作": "主体在做什么（10字内）",
 "景别": "特写/近景/中景/全景/远景",
 "构图": "主体在画面中的位置（居中/偏左/偏右/贴边/上下留白）",
 "光线": "光从哪来、硬还是软、有没有过曝（15字内）",
 "可辨认度": 0.0,
 "适合做钩子": false,
 "适合做结尾": false,
 "字幕安全区冲突": "无",
 "画面压字或水印": "无",
 "美学分": 0.0,
 "点评": "十个字内"}
其中 可辨认度/美学分 是 0.0-1.0 的小数，「字幕安全区冲突」只能填 无/与脸冲突/与主体冲突，
「画面压字或水印」没有就填 无，有就写清位置。"""

# v3.2-B6：内容描述 + 三封闭槽位 合并为一个 JSON schema（一次 VLM 调用拿全）。
# 比原 13 字段 SCHEMA 精简：只要 content + 三槽位 + emotion/confidence，并额外要 可辨认度/美学分
# 以兼容 review_film / summarize。短输出配合 num_predict=640 + think:false，稳定秒回。
MERGED_PROMPT = """看这一帧画面。只输出一个 JSON 对象，不要解释，不要代码块：
{"content": "主体是谁、在做什么（20字内）",
 "shot_size": "...", "shot_angle": "...", "shot_motion": "...",
 "emotion_tag": "...", "confidence": 0.8,
 "可辨认度": 0.8, "美学分": 0.8}
其中：
content 是一句话描述（主体+动作），20字内；
shot_size 只能填：extreme_close_up / close_up / medium / wide / extreme_wide
shot_angle 只能填：high_angle / low_angle / eye_level / overhead / aerial
shot_motion 只能填：locked / handheld / tilt / zoom / pan / static
emotion_tag 只能填：calm / tense / warm / neutral（不确定就 neutral）
confidence / 可辨认度 / 美学分 都是 0.0-1.0 的小数。"""

# v3.9.0-Stepwise：分步提问的两问（SHOTPROF_STEPWISE=1 时启用；默认不用）
# 第一步只问"画面主体/内容"，第二步问三槽位+美学。实测收益留待 A/B 后再决定是否设默认。
STEP1_CONTENT_PROMPT = """看这一帧画面。只输出一个 JSON 对象，不要解释，不要代码块：
{"content": "主体是谁、在做什么（20字内）"}"""
STEP2_SLOTS_PROMPT = """看这一帧画面。只输出一个 JSON 对象，不要解释，不要代码块：
{"shot_size": "...", "shot_angle": "...", "shot_motion": "...",
 "emotion_tag": "...", "confidence": 0.8, "可辨认度": 0.8, "美学分": 0.8}
其中：
shot_size 只能填：extreme_close_up / close_up / medium / wide / extreme_wide
shot_angle 只能填：high_angle / low_angle / eye_level / overhead / aerial
shot_motion 只能填：locked / handheld / tilt / zoom / pan / static
emotion_tag 只能填：calm / tense / warm / neutral（不确定就 neutral）
confidence / 可辨认度 / 美学分 都是 0.0-1.0 的小数。"""

KEY_UNDERSTANDING = ("主体", "动作", "景别", "构图", "光线", "可辨认度", "适合做钩子",
                     "适合做结尾", "字幕安全区冲突", "画面压字或水印", "点评")

# ---------- v3.4：MERGED 输出的 8 字段强校验 + 缺失补全 ----------
# 实测本地视觉模型常只回 5 个字段，emotion_tag/shot_motion/confidence 缺失、动作为空。
# 这里把"应拿到的 8 个字段"显式列出，缺谁补谁，补不上再用枚举兜底。
MERGED_FIELDS = ("content", "shot_size", "shot_angle", "shot_motion",
                 "emotion_tag", "confidence", "可辨认度", "美学分")
# 补全重问仍失败时的兜底默认值（按任务约定）
MERGED_FALLBACK = {
    "shot_size": "medium", "shot_angle": "eye_level", "shot_motion": "locked",
    "emotion_tag": "neutral", "confidence": 0.3, "可辨认度": 0.5, "美学分": 0.5,
}


def _field_present(value) -> bool:
    """判断单个字段是否真的从模型拿到了有效值：None/空串都算缺失，数值恒算有效。"""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def _missing_merged_fields(raw: dict) -> list[str]:
    """从 8 个必需字段里挑出模型这一轮没答全的。"""
    return [k for k in MERGED_FIELDS if not _field_present(raw.get(k))]


def _b64_size_ok(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _drawtext_filter(label: str) -> str:
    """build drawtext 滤镜：左下角半透明黑底白字标签（如 "12.5s"）。fontfile 转义冒号。"""
    font = r"C:\Windows\Fonts\arial.ttf"
    if not Path(font).exists():
        font = r"C:\Windows\Fonts\segoeui.ttf"
    font_arg = font.replace("\\", "/").replace(":", "\\:")
    return (f"drawtext=text='{label}':fontfile='{font_arg}':x=10:y=h-text_h-10:"
            f"fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=4")


def extract_frame(video: Path, t: float, out: Path,
                  burn_timestamp: bool | None = None) -> bool:
    """从视频 t 秒抽一帧。burn_timestamp=True 时把秒数烧进帧左下角（v3.9.0 技巧①）。

    burn_timestamp 缺省时读环境变量 SHOTPROF_BURN_TS（默认关闭——烧字会干扰本机主体框
    与后续人工复核，仅在调试语义细分/人工标时间点时开启）。
    """
    if burn_timestamp is None:
        burn_timestamp = os.environ.get("SHOTPROF_BURN_TS", "0") == "1"
    vf = f"scale={FRAME_W}:-1"
    if burn_timestamp:
        vf += "," + _drawtext_filter(f"{max(t, 0):.1f}s")
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{max(t, 0):.3f}", "-i", str(video),
           "-frames:v", "1", "-vf", vf, str(out)]
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return _b64_size_ok(out)


def _sample_frames_dense(video: Path, shot: dict, tmp: Path, *,
                         n: int, min_len: float, ham_thresh: int) -> dict:
    """v3.9.0 技巧②：长镜头（>min_len 秒）均匀抽 n 帧，相邻帧 ahash 去重。

    只做"帧采样 + 本地记录"，不把额外帧交给 VLM（VLM 仍只看 best_frame）。
    返回 {"requested", "times", "kept"}；镜头不够长或抽帧失败时返回空记录。
    """
    length = float(shot.get("len") or 0.0)
    if length < min_len or n <= 0:
        return {"requested": 0, "times": [], "kept": 0}
    t0, t1 = float(shot["t0"]), float(shot["t1"])
    if n == 1:
        times = [(t0 + t1) / 2]
    else:
        times = [round(t0 + (t1 - t0) * i / (n - 1), 2) for i in range(n)]
    kept: list[float] = []
    prev_hash: str | None = None
    for k, t in enumerate(times):
        p = tmp / f"dense_{shot['i']:03d}_{k:02d}.jpg"
        if not extract_frame(video, t, p):
            continue
        with Image.open(p) as im:
            h = _ahash(im)
        if prev_hash is not None and _hamming(prev_hash, h) < ham_thresh:
            continue  # 与上一帧几乎一样，相邻去重
        kept.append(t)
        prev_hash = h
    return {"requested": len(times), "times": kept, "kept": len(kept)}


def _saliency_box(img: Image.Image, skin_threshold: float = 0.02) -> dict:
    """本地估主体框：中心-周边对比 + 肤色，取并集的外接框（比例坐标）。没有 cv2 也能用。"""
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    small = gray[::4, ::4]
    blur = np.asarray(Image.fromarray(gray.astype(np.uint8)).filter(
        __import__("PIL.ImageFilter", fromlist=["ImageFilter"]).GaussianBlur(12)), dtype=np.float32)
    sal = np.abs(gray - blur)[::4, ::4]
    thr = np.percentile(sal, 85)
    mask = sal >= max(thr, 6.0)

    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)[::4, ::4]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    skin = ((r > 95) & (g > 40) & (b > 20) & (mx - mn > 15) & (np.abs(r - g) > 15) & (r > g) & (r > b))
    skin_ratio = float(skin.mean())
    if skin_ratio >= skin_threshold:
        mask = mask | skin

    h, w = mask.shape
    if not mask.any():
        return {"框": [0.0, 0.0, 1.0, 1.0], "占比": 0.0, "肤色占比": round(skin_ratio, 4), "来源": "无"}
    ys, xs = np.where(mask)
    box = [round(float(xs.min()) / w, 3), round(float(ys.min()) / h, 3),
           round(float(xs.max() + 1) / w, 3), round(float(ys.max() + 1) / h, 3)]
    return {"框": box, "占比": round(float(mask.mean()), 4), "肤色占比": round(skin_ratio, 4),
            "来源": "显著性+肤色"}


def _safe_area(box: list[float], skin_ratio: float) -> tuple[str, float]:
    """主体框和字幕带的重叠程度 → 冲突判定。"""
    if not box:
        return "无", 0.0
    y0, y1 = box[1], box[3]
    band_top = 1.0 - SUB_BAND
    overlap = max(0.0, min(y1, 1.0) - max(y0, band_top)) / max(y1 - y0, 1e-6)
    if overlap <= 0.05:
        return "无", round(overlap, 3)
    return ("与脸冲突" if skin_ratio >= 0.02 else "与主体冲突"), round(overlap, 3)


def _static_overlay(frames: list[np.ndarray]) -> dict:
    """水印/压字嫌疑：多个镜头里同一角落边缘能量都高 = 固定叠层。"""
    if len(frames) < 3:
        return {"嫌疑": "样本不足", "位置": ""}
    h, w = frames[0].shape
    corners = {"左上": (0, 0, w // 4, h // 8), "右上": (3 * w // 4, 0, w, h // 8),
               "左下": (0, 7 * h // 8, w // 4, h), "右下": (3 * w // 4, 7 * h // 8, w, h),
               "底部": (w // 4, 7 * h // 8, 3 * w // 4, h)}
    scores = {}
    for name, (x0, y0, x1, y1) in corners.items():
        vals = []
        for f in frames:
            crop = f[y0:y1, x0:x1]
            gy, gx = np.gradient(crop)
            vals.append(float(np.abs(gx).mean() + np.abs(gy).mean()))
        scores[name] = round(float(np.median(vals)), 2)
    if not scores:
        return {"嫌疑": "无", "位置": ""}
    top = max(scores, key=scores.get)
    others = [v for k, v in scores.items() if k != top]
    if scores[top] > 6 and scores[top] > (np.median(others) + 3 if others else 0):
        return {"嫌疑": "有", "位置": top, "分数": scores[top]}
    return {"嫌疑": "无", "位置": ""}


def _ahash(img: Image.Image, size: int = 8) -> str:
    g = np.asarray(img.convert("L").resize((size, size)), dtype=np.float32)
    bits = (g > g.mean()).flatten()
    return "".join("1" if b else "0" for b in bits)


def _hamming(a: str, b: str) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


def _clamp_enum(value, enum_dict: dict, default: str, cn_map: dict | None = None) -> str:
    """把模型输出归一化到封闭枚举：精确命中→直接用；中文兜底映射；否则 default。"""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in enum_dict:
            return v
        if cn_map:
            for cn, en in cn_map.items():
                if cn in value:
                    return en
    return default


def _classify_three_slots(frame: Path, use_cloud: bool, fallback_size_cn: str = "") -> dict:
    """TOP4：一次视觉调用把当前镜头分到三个封闭槽位。失败时全部降级默认值。"""
    out = {"shot_size": "medium", "shot_angle": "eye_level", "shot_motion": "locked",
           "emotion_tag": "neutral", "confidence": 0.3}
    try:
        provider = "auto" if use_cloud else "ollama"
        raw = llm.vision_json(frame, THREE_SLOT_PROMPT, provider=provider)
        out["shot_size"] = _clamp_enum(
            raw.get("shot_size"), SHOT_SIZES,
            _clamp_enum(fallback_size_cn, SHOT_SIZES, "medium", CN_SIZE_MAP),
            CN_SIZE_MAP)
        out["shot_angle"] = _clamp_enum(raw.get("shot_angle"), SHOT_ANGLES, "eye_level")
        out["shot_motion"] = _clamp_enum(raw.get("shot_motion"), SHOT_MOTIONS, "locked")
        emo = raw.get("emotion_tag")
        out["emotion_tag"] = emo if emo in SHOT_EMOTIONS else "neutral"
        conf = raw.get("confidence")
        out["confidence"] = round(float(conf), 2) if isinstance(conf, (int, float)) else 0.5
        out["_provider"] = raw.get("_provider", "?")
    except Exception as exc:  # noqa: BLE001
        out["_error"] = f"{type(exc).__name__}: {exc}"[:120]
        out["shot_size"] = _clamp_enum(fallback_size_cn, SHOT_SIZES, "medium", CN_SIZE_MAP)
    return out


def _understand(frame: Path, use_cloud: bool) -> tuple[dict, str]:
    """语义理解：云端视觉模型优先，失败回退本地。返回（字段, 来源标记）。"""
    provider = "auto" if use_cloud else "ollama"
    try:
        raw = llm.vision_json(frame, SCHEMA_PROMPT, provider=provider)
        got = {k: raw.get(k) for k in KEY_UNDERSTANDING if k in raw}
        aesthetic = raw.get("美学分")
        got["美学分"] = float(aesthetic) if isinstance(aesthetic, (int, float)) else None
        return got, f"vlm:{raw.get('_provider', '?')}"
    except Exception as exc:  # noqa: BLE001
        return {"错误": f"{type(exc).__name__}: {exc}"[:160]}, ""


# 中文景别 -> 反向给 shot_size 英文枚举（B6 合并后由 shot_size 反推"景别"字段，保向后兼容）
_SIZE_EN2CN = {v: k for k, v in CN_SIZE_MAP.items()}


def _understand_merged(frame: Path, use_cloud: bool) -> tuple[dict, str, dict]:
    """v3.2-B6：一次视觉调用同时拿到内容描述 + 三个封闭槽位（含可辨认度/美学分保兼容）。
    返回 (understanding, source, slots)。失败时 understanding 带错误、slots 全默认。"""
    out_slots = {"shot_size": "medium", "shot_angle": "eye_level", "shot_motion": "locked",
                 "emotion_tag": "neutral", "confidence": 0.3,
                 "_field_completeness": 0.0, "_retry_count": 0}
    provider = "auto" if use_cloud else "ollama"
    try:
        # v3.9.0 技巧③：分步提问（SHOTPROF_STEPWISE=1）。默认仍走 MERGED_PROMPT 一步模式。
        if STEPWISE_MODE:
            raw: dict = {}
            try:  # 第一步：只问主体/内容
                raw.update(llm.vision_json(frame, STEP1_CONTENT_PROMPT, provider=provider))
            except Exception:  # noqa: BLE001
                pass
            try:  # 第二步：三槽位 + 美学
                raw.update(llm.vision_json(frame, STEP2_SLOTS_PROMPT, provider=provider))
            except Exception:  # noqa: BLE001
                pass
        else:
            raw = llm.vision_json(frame, MERGED_PROMPT, provider=provider)
        # ---- v3.4：8 字段强校验——缺字段就用同一张图补全重问一次 ----
        retry_count = 0
        missing = _missing_merged_fields(raw)
        if missing:
            retry_count = 1
            ask = "、".join(missing)
            retry_prompt = (
                f"上一次回答缺少 {ask} 字段。请只输出这些缺失字段组成的一个 JSON 对象，"
                f"不要重复其它字段，不要解释，不要代码块："
                + json.dumps({k: "..." for k in missing}, ensure_ascii=False)
            )
            try:
                raw2 = llm.vision_json(frame, retry_prompt, provider=provider)
                for k in missing:  # 只补缺失的字段，不覆盖已拿到的
                    if _field_present(raw2.get(k)):
                        raw[k] = raw2[k]
            except Exception:  # noqa: BLE001  补全失败就走兜底
                pass
        # 统计 8 字段实际从模型拿到的比例（跨主问+补全重问）
        got = sum(1 for k in MERGED_FIELDS if _field_present(raw.get(k)))
        completeness = round(got / len(MERGED_FIELDS), 3)
        out_slots["_field_completeness"] = completeness
        out_slots["_retry_count"] = retry_count
        content = str(raw.get("content", "") or "").strip()
        if not content:  # content 仍为空：标"待补"，避免空描述污染下游决策
            content = "待补"
        source = f"vlm:{raw.get('_provider', '?')}"
        # 三槽位（封闭枚举强校验）
        out_slots["shot_size"] = _clamp_enum(raw.get("shot_size"), SHOT_SIZES, "medium")
        out_slots["shot_angle"] = _clamp_enum(raw.get("shot_angle"), SHOT_ANGLES, "eye_level")
        out_slots["shot_motion"] = _clamp_enum(raw.get("shot_motion"), SHOT_MOTIONS, "locked")
        emo = raw.get("emotion_tag")
        out_slots["emotion_tag"] = emo if emo in SHOT_EMOTIONS else "neutral"
        conf = raw.get("confidence")
        out_slots["confidence"] = round(float(conf), 2) if isinstance(conf, (int, float)) else MERGED_FALLBACK["confidence"]
        out_slots["_provider"] = raw.get("_provider", "?")
        # 反推"理解"里的兼容字段
        head, _, tail = content.partition("，")
        rkb = raw.get("可辨认度")
        aesthetic = raw.get("美学分")
        understanding = {
            "主体": head.strip(),
            "动作": tail.strip(),
            "景别": _SIZE_EN2CN.get(out_slots["shot_size"], ""),
            "可辨认度": float(rkb) if isinstance(rkb, (int, float)) else MERGED_FALLBACK["可辨认度"],
            "美学分": float(aesthetic) if isinstance(aesthetic, (int, float)) else MERGED_FALLBACK["美学分"],
            "_field_completeness": completeness,
            "_retry_count": retry_count,
        }
        return understanding, source, out_slots
    except Exception as exc:  # noqa: BLE001
        return {"错误": f"{type(exc).__name__}: {exc}"[:160]}, "", out_slots


def _semantic_split(video: Path, stream: dict, shots: list[dict],
                    *, min_len: float = 4.0, min_gap: float = 2.5,
                    sigma: float = 1.2) -> list[dict]:
    """2.0 升级一：固定机位/口播素材的语义细分。

    固定机位口播通常整条只有 1~2 个镜头（ffmpeg scene 检不出切点），
    镜头档案因此退化成一整条，无法驱动"逐镜头决策"。这里在画面帧差曲线上
    找动作/内容切换点（低头、抬头、转身、展示、翻页等），把大镜头切成
    若干语义段，每段交给视觉模型独立理解。
    """
    duration = float(stream.get("duration") or 0.0)
    # v3.4：旧门槛要求 duration>=20s，12~19.9s 口播素材全被跳过、整条只当 1 个镜头；
    # 新门槛改为时长>=6.0s 且镜头数<=2 即启用细分（min_len=4.0 仍防碎片）。
    if duration < 6.0 or len(shots) > 2:
        return shots
    tmp = Path(tempfile.mkdtemp(prefix="aicut-split-"))
    try:
        frames = vision.extract_frames(video, tmp, fps=2.0, width=320)
        if len(frames) < 8:
            return shots
        diffs: list[float] = []
        prev: np.ndarray | None = None
        for p in frames:
            with Image.open(p) as im:
                g = np.asarray(im.convert("L"), dtype=np.float32)
            diffs.append(0.0 if prev is None else float(np.mean(np.abs(g - prev))))
            prev = g
        arr = np.array(diffs, dtype=np.float64)
        kernel = np.ones(3) / 3.0
        smooth = np.convolve(arr, kernel, mode="same")
        thr = float(smooth.mean() + sigma * smooth.std())
        peaks: list[int] = []
        for i in range(2, len(smooth) - 2):
            if smooth[i] > thr and smooth[i] >= smooth[i - 1] and smooth[i] > smooth[i + 1]:
                peaks.append(i)
        merged: list[int] = []
        for c in peaks:
            if merged and (c - merged[-1]) * 0.5 < min_gap:
                continue
            merged.append(c)
        times = [round(c / 2.0, 2) for c in merged]
        times = [t for t in times if min_len < t < duration - min_len]
        bounds = [0.0] + times + [duration]
        new_shots: list[dict] = []
        for i in range(len(bounds) - 1):
            t0, t1 = bounds[i], bounds[i + 1]
            if t1 - t0 < min_len:
                continue
            new_shots.append({
                "i": len(new_shots), "t0": round(t0, 2), "t1": round(t1, 2),
                "len": round(t1 - t0, 2), "frames": int(round((t1 - t0) * 2)),
                "best_t": round((t0 + t1) / 2, 2), "motion": 0.0,
            })
        if len(new_shots) > len(shots):
            return vision._group_shots(stream['frames'], times, duration)
        return shots
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build(video: str | Path, *, max_shots: int | None = None, use_cloud: bool = True,
          frames_per_shot: int = 3, force: bool = False, progress=print,
          preframes: list[dict] | None = None,
          quality_first: bool = False) -> dict:
    """生成（或读缓存）整条素材的镜头档案。

    v3.2 提速：
      · B6  内容描述 + 三槽位合并为一次 VLM 调用（原 2 次/镜头 -> 1 次/镜头）
      · B7  感知哈希预判：与上一个真正分析过的镜头太像就跳过 VLM，复用上一镜头
      · B8  多个"需要 VLM"的镜头用线程池并发（默认 2），配合 OLLAMA_NUM_PARALLEL
    """
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(video)
    fp = fingerprint(video)
    import hashlib
    analysis_signature = hashlib.sha256(json.dumps({
        'version': 3, 'model': llm.get_vision_model(), 'prompt': MERGED_PROMPT,
        'use_cloud': use_cloud, 'max_shots': max_shots,
        'quality_first': quality_first,
    }, sort_keys=True).encode()).hexdigest()
    mem = open_memory()
    # TOP4：结构化三槽位文件缓存（work/shotprof-<素材名>-structured.json）
    struct_cache = WORK / f"shotprof-{video.stem}-structured.json"
    # A quality-first pass may reuse an earlier *complete* semantic scan only
    # when the source and current vision-model signature still match. Re-run
    # full media decode and shot coverage below; this cache does not replace
    # source-integrity verification.
    if not force and struct_cache.exists():
        try:
            loaded = json.loads(struct_cache.read_text(encoding="utf-8-sig"))
            gate = loaded.get('visual_coverage') or quality_coverage(loaded)
            cached_signature = loaded.get('analysis_signature')
            if (loaded.get("fingerprint") == fp
                    and cached_signature == analysis_signature
                    and (not quality_first or gate.get('complete'))):
                loaded["cached"] = True
                loaded["file"] = str(struct_cache)
                return loaded
        except Exception:  # noqa: BLE001
            pass
    if not force and not quality_first:
        cached = mem.get_visual(f"shotprof:{fp}:{analysis_signature}")
        if cached:
            cached["cached"] = True
            return cached

    # v3.2-B8：提示 Ollama 服务端可并发处理（进程内 env 仅对"本进程新起的 ollama"生效；
    # 已在跑的服务需重启并带 OLLAMA_NUM_PARALLEL=2 才真正并行，否则服务端会排队）。
    os.environ.setdefault("OLLAMA_NUM_PARALLEL", "2")

    # Quality-first runs recalculate full-frame metrics and never trust an older,
    # partial vision cache as evidence of complete coverage.
    stream = vision.analyze(video, use_cache=not quality_first)
    shots = stream.get("shots") or []
    if not shots:
        return {"video": str(video), "fingerprint": fp, "shots": [],
                "summary": {"shots": 0, "hints": ["没有识别到镜头，检查素材"]}, "cached": False}
    # 2.0 升级一：固定机位/口播素材按动作变化细分镜头
    shots = _semantic_split(video, stream, shots)

    tmp = Path(tempfile.mkdtemp(prefix="aicut-shotprof-"))
    grays: list[np.ndarray] = []
    hashes: dict[int, str] = {}
    profiles: list[dict] = []
    prepared: list[dict] = []
    try:
        # ===== 第一遍：抽帧 + 本机分析（纯本地，不调 VLM）=====
        for shot in shots:
            idx = shot["i"]
            times = [shot["best_t"], shot.get("face_t", shot["best_t"]),
                     round((shot["t0"] + shot["t1"]) / 2, 3)]
            if preframes:
                times = [fr["timestamp"] for fr in preframes
                         if shot["t0"] - 1e-6 <= fr["timestamp"] < shot["t1"] - 1e-6] \
                        or [shot["best_t"]]
            seen: list[float] = []
            local: dict = {}
            frame_paths: list[Path] = []
            for t in times:
                if any(abs(t - s) < 0.2 for s in seen):
                    continue
                seen.append(t)
                p = tmp / f"s{idx:03d}_{t:.2f}.jpg"
                if extract_frame(video, t, p):
                    frame_paths.append(p)
            if frame_paths:
                with Image.open(frame_paths[0]) as img:
                    local["主体框"] = _saliency_box(img)
                    hashes[idx] = _ahash(img)
                    conflict, overlap = _safe_area(local["主体框"]["框"],
                                                   local["主体框"]["肤色占比"])
                    local["安全区"] = {"冲突": conflict, "重叠": overlap}
                    grays.append(np.asarray(img.convert("L"), dtype=np.float32)[::4, ::4])
                brights = []
                for p in frame_paths:
                    with Image.open(p) as im:
                        arr = np.asarray(im.convert("L"), dtype=np.float32)
                    brights.append(float(arr.mean()))
                if len(brights) >= 2:
                    local["闪烁"] = round(float(np.std(brights)), 2)
                local["抖动"] = round(float(shot.get("motion") or 0.0), 2)
            # v3.9.0 技巧②：长镜头密采样（SHOTPROF_DENSE_FRAMES>0 时启用；默认关闭）。
            # 只记录采样结果，不改 VLM 调用。
            if DENSE_FRAMES > 0:
                local["密采样"] = _sample_frames_dense(
                    video, shot, tmp, n=DENSE_FRAMES,
                    min_len=DENSE_MIN_LEN, ham_thresh=DENSE_HAM)
            prepared.append({"shot": shot, "idx": idx, "frame_paths": frame_paths,
                             "best_frame": frame_paths[0] if frame_paths else None,
                             "local": local, "seen": seen})

        # ===== B7：感知哈希预判——与上一个"真正分析过"的镜头太像则跳过 VLM =====
        NEED, SKIP, NODATA, CAP = "need", "similar", "no_frame", "cap"
        plan: list[tuple[str, int]] = []
        prev_hash: str | None = None
        need_count = 0
        for i, prep in enumerate(prepared):
            if not prep["frame_paths"]:
                plan.append((NODATA, i)); continue
            if max_shots is not None and need_count >= max_shots:
                plan.append((CAP, i)); continue
            h = hashes.get(prep["idx"])
            if (not quality_first and HAM_SKIP_ENABLED and prev_hash is not None and h is not None
                    and _hamming(prev_hash, h) < HAM_SIMILAR_DIST):
                plan.append((SKIP, i))
            else:
                plan.append((NEED, i))
                prev_hash = h
                need_count += 1

        # ===== B6+B8：并发调 VLM（一次调用同时拿内容描述+三槽位）=====
        results: dict[int, dict] = {}

        def _analyze(prep: dict) -> dict:
            try:
                if quality_first:
                    import hashlib
                    evidence_dir = WORK / 'visual-evidence' / fp
                    evidence_dir.mkdir(parents=True, exist_ok=True)
                    evidence_path = evidence_dir / prep['best_frame'].name
                    shutil.copy2(prep['best_frame'], evidence_path)
                    prep['semantic_evidence'] = {
                        'image': str(evidence_path),
                        'sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                        'timestamp': prep['seen'][0],
                        'model': llm.get_vision_model(),
                        'status': 'automatic_visual_observation_unreviewed',
                    }
                understanding, source, slots = _understand_merged(prep["best_frame"], use_cloud)
                return {"idx": prep["idx"], "understanding": understanding,
                        "source": source, "slots": slots}
            except Exception as exc:  # noqa: BLE001
                return {"idx": prep["idx"], "understanding": {
                            "错误": f"{type(exc).__name__}: {str(exc)[:200]}"},
                        "source": "", "slots": {}}

        need_jobs = [prepared[i] for st, i in plan if st == NEED]
        workers = max(1, min(SHOTPROF_WORKERS, len(need_jobs)))
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(_analyze, prep) for prep in need_jobs]
                for fut in as_completed(futs):
                    r = fut.result()
                    results[r["idx"]] = r
                    progress(f"[视觉进度] {len(results)}/{len(need_jobs)} 镜头已返回；镜头 {r['idx']}")
        else:
            for prep in need_jobs:
                r = _analyze(prep)
                results[r["idx"]] = r
                progress(f"[视觉进度] {len(results)}/{len(need_jobs)} 镜头已返回；镜头 {r['idx']}")

        # ===== 按镜头顺序组装 profile（顺序与字段向后兼容）=====
        last = {"understanding": {}, "source": "", "slots": {}}
        for i, prep in enumerate(prepared):
            shot = prep["shot"]; idx = prep["idx"]; local = prep["local"]; seen = prep["seen"]
            status = plan[i][0]
            similar = False
            if status == NEED:
                r = results[idx]
                understanding, source, slots = r["understanding"], r["source"], r["slots"]
                last = {"understanding": understanding, "source": source, "slots": slots}
                analysis_status = ("analyzed" if source and not understanding.get("错误")
                                   and understanding.get("主体") not in (None, '', '待补')
                                   and slots.get('_field_completeness', 0) == 1 else "failed")
                progress(f"  镜头 {idx}（{shot['t0']:.1f}-{shot['t1']:.1f}s）"
                         f" 可辨认度 {understanding.get('可辨认度', '?')} · "
                         f"{understanding.get('主体', '')[:16]}")
                progress(f"    三槽位 {slots.get('shot_size')}/{slots.get('shot_angle')}/"
                         f"{slots.get('shot_motion')} conf={slots.get('confidence')}")
            elif status == SKIP:
                understanding = dict(last["understanding"])
                if understanding.get("主体"):
                    understanding["主体"] = str(understanding["主体"]) + "（相似帧）"
                source = last["source"]
                slots = dict(last["slots"])
                similar = True
                analysis_status = "reused_similar"
                progress(f"  镜头 {idx}（{shot['t0']:.1f}-{shot['t1']:.1f}s）画面与上镜相似，"
                         f"跳过 VLM，复用上一镜头分类")
            else:
                understanding, source = {}, ""
                analysis_status = "frame_missing" if status == NODATA else "shot_cap_reached"
                slots = {"shot_size": "medium", "shot_angle": "eye_level", "shot_motion": "locked",
                         "emotion_tag": "neutral", "confidence": 0.3,
                         "_field_completeness": 0.0, "_retry_count": 0}
            content = str(understanding.get("主体", "") or "")
            if understanding.get("动作"):
                content += "，" + str(understanding["动作"])
            profiles.append({
                # ---- 原有字段全部保留（向后兼容 review_film._score_picture 等） ----
                "shot": idx,
                "t0": shot["t0"], "t1": shot["t1"], "len": shot["len"],
                "指标": {k: shot.get(k) for k in ("blur", "center_blur", "motion", "skin",
                                                  "bright", "grade", "exposure")},
                "本机分析": local,
                "理解": understanding,
                "美学": {"评分": understanding.get("美学分"), "点评": understanding.get("点评", "")},
                "取帧": [round(t, 2) for t in seen],
                "来源": [source] if source else [],
                "可信度": 0.85 if source.startswith("vlm:deepseek") else (0.6 if source else 0.3),
                # ---- TOP4：结构化三槽位（新字段，旧调用方不读不受影响） ----
                "shot_id": f"shot_{idx:03d}",
                "time_range": [round(shot["t0"], 2), round(shot["t1"], 2)],
                "shot_size": slots.get("shot_size", "medium"),
                "shot_angle": slots.get("shot_angle", "eye_level"),
                "shot_motion": slots.get("shot_motion", "locked"),
                "content": content[:60],
                "subtitle": "",
                "emotion_tag": slots.get("emotion_tag", "neutral"),
                "confidence": slots.get("confidence", 0.3),
                # ---- v3.4：该镜头 8 字段完整率，供 summary 求平均 ----
                "_field_completeness": slots.get("_field_completeness", 0.0),
                "_retry_count": slots.get("_retry_count", 0),
                "_similar": similar,
                "_analysis_status": analysis_status,
                "_analysis_error": str(understanding.get("错误", ""))[:240],
                "semantic_evidence": prep.get('semantic_evidence'),
            })
        overlay = _static_overlay(grays)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    dup_groups: list[list[int]] = []
    ids = sorted(hashes)
    used: set[int] = set()
    for i, a in enumerate(ids):
        if a in used:
            continue
        group = [a]
        for b in ids[i + 1:]:
            if b in used:
                continue
            if _hamming(hashes[a], hashes[b]) <= 3:
                group.append(b)
                used.add(b)
        if len(group) > 1:
            dup_groups.append(group)

    for prof in profiles:
        if overlay.get('嫌疑') == '有':
            prof['本机分析']['水印嫌疑'] = overlay

    result = {
        'video': str(video), 'fingerprint': fp,
        'analysis_signature': analysis_signature,
        'duration': stream.get('duration'), 'shots': profiles,
        'summary': summarize(profiles, dup_groups, overlay),
        'cached': False,
    }
    result['visual_coverage'] = quality_coverage(result, stream)
    mem.cache_visual(f'shotprof:{fp}:{analysis_signature}', str(video), result)
    out = WORK / f'shotprof-{video.stem}.json'
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    result['file'] = str(out)
    try:
        struct_cache.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                encoding='utf-8')
        result['struct_cache'] = str(struct_cache)
    except Exception:  # noqa: BLE001
        pass
    return result


def quality_coverage(result: dict, stream: dict | None = None) -> dict:
    """Auditable gate for complete frame metrics and per-shot visual understanding."""
    import math
    stream = stream or {}
    duration = float(result.get('duration') or stream.get('duration') or 0)
    fps = float(stream.get('fps') or vision.DEFAULT_FPS)
    frames = list(stream.get('frames') or [])
    shots = list(result.get('shots') or [])
    expected = max(1, int(duration * fps) - 1) if duration > 0 else 0
    first_t = float(frames[0].get('t', -1)) if frames else -1
    last_t = float(frames[-1].get('t', -1)) if frames else -1
    sample_times = [float(row.get('t', -1)) for row in frames]
    max_gap = max((b - a for a, b in zip(sample_times, sample_times[1:])), default=0.0)
    frame_complete = bool(
        math.isfinite(duration) and duration > 0 and math.isfinite(fps) and fps > 0
        and all(math.isfinite(t) and 0 <= t <= duration for t in sample_times)
        and all(b > a for a, b in zip(sample_times, sample_times[1:]))
        and expected and len(frames) >= expected and first_t <= 0.5 / max(fps, 0.1)
        and last_t >= max(0.0, duration - 1.5 / max(fps, 0.1))
        and max_gap <= 1.5 / max(fps, 0.1))
    uncovered = [
        {'shot': p.get('shot'), 'start': p.get('t0'), 'end': p.get('t1'),
         'status': p.get('_analysis_status', 'unknown'),
         'error': p.get('_analysis_error', '')}
        for p in shots if p.get('_analysis_status') != 'analyzed'
    ]
    issues = []
    if not frame_complete:
        issues.append('全片帧级扫描覆盖不完整')
    if not shots:
        issues.append('未检测到可分析镜头')
    if uncovered:
        issues.append('存在未完成独立视觉理解的镜头')
    end = 0.0
    for shot in shots:
        a, b = float(shot.get('t0', -1)), float(shot.get('t1', -1))
        if not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b <= duration + .02
                and abs(a - end) <= .02):
            issues.append('镜头时间轴存在缺口、重叠或非法区间')
            break
        end = b
    if shots and abs(end - duration) > .02:
        issues.append('镜头未覆盖完整片尾')
    return {
        'status': 'complete' if not issues else 'incomplete',
        'complete': not issues,
        'semantic_verified': False,
        'scope': 'sample and shot coverage only; model descriptions require verification',
        'duration': round(duration, 3), 'fps': fps,
        'expected_frame_samples_min': expected,
        'frame_samples': len(frames),
        'first_sample_t': first_t if frames else None,
        'last_sample_t': last_t if frames else None,
        'max_sample_gap_seconds': round(max_gap, 3),
        'shot_count': len(shots),
        'independently_understood_shots': sum(
            1 for p in shots if p.get('_analysis_status') == 'analyzed'),
        'uncovered_shots': uncovered,
        'issues': issues,
    }


def summarize(profiles: list[dict], dup_groups: list[list[int]] | None = None,
              overlay: dict | None = None) -> dict:
    """给决策用的浓缩版：谁能当钩子、谁不能用、哪里会挡字幕。"""
    hook, ending, weak, conflicts, watermarks = [], [], [], [], []
    for p in profiles:
        u = p.get("理解") or {}
        r = u.get("可辨认度")
        r = float(r) if isinstance(r, (int, float)) else None
        rec = {"shot": p["shot"], "t": p["t0"], "可辨认度": r,
               "美学分": (p.get("美学") or {}).get("评分"), "主体": u.get("主体", "")}
        if u.get("适合做钩子") is True and (r is None or r >= 0.6):
            hook.append(rec)
        if u.get("适合做结尾") is True:
            ending.append(rec)
        if (r is not None and r < 0.45) or p["指标"].get("grade") == "糊":
            weak.append({"shot": p["shot"], "t0": p["t0"], "t1": p["t1"],
                         "原因": "可辨认度低" if (r is not None and r < 0.45) else "画面糊"})
        conflict = (p.get("本机分析") or {}).get("安全区") or {}
        if conflict.get("冲突") not in (None, "无"):
            conflicts.append({"shot": p["shot"], "t0": p["t0"], "冲突": conflict["冲突"],
                              "重叠": conflict.get("重叠")})
        if (p.get("本机分析") or {}).get("水印嫌疑", {}).get("嫌疑") == "有":
            watermarks.append({"shot": p["shot"], "位置": p["本机分析"]["水印嫌疑"].get("位置")})
    hook.sort(key=lambda x: (-(x["美学分"] or 0), -(x["可辨认度"] or 0)))
    hints = hints_from(profiles, hook, weak, conflicts, dup_groups or [], overlay or {})
    # v3.4：所有镜头的 8 字段平均完整率（衡量 VLM 输出质量）
    comps = [p["_field_completeness"] for p in profiles
             if isinstance(p.get("_field_completeness"), (int, float))]
    vlm_completeness = round(sum(comps) / len(comps), 3) if comps else 0.0
    return {
        "shots": len(profiles),
        "vlm_field_completeness": vlm_completeness,
        "hook_candidates": hook[:5],
        "ending_candidates": ending[:3],
        "weak_shots": weak,
        "safe_area_conflicts": conflicts,
        "watermarks": watermarks,
        "duplicate_groups": dup_groups or [],
        "static_overlay": overlay or {},
        "hints": hints,
    }


def hints_from(profiles: list[dict], hook: list[dict], weak: list[dict],
               conflicts: list[dict], dup_groups: list[list[int]], overlay: dict) -> list[str]:
    hints: list[str] = []
    if hook:
        first = hook[0]
        hints.append(f"镜头 {first['shot']}（{first['t']:.1f}s）最适合做钩子："
                     f"可辨认度 {first['可辨认度']}、美学 {first['美学分']}、{first['主体'][:14]}")
    if weak:
        hints.append("以下镜头可辨认度低/糊，别单独用，建议覆盖 B-roll 或缩短："
                     + "、".join(f"#{w['shot']}({w['t0']:.1f}-{w['t1']:.1f}s)" for w in weak[:5]))
    if conflicts:
        hints.append("字幕会和主体打架，字幕上移或缩字号："
                     + "、".join(f"#{c['shot']}（{c['冲突']}）" for c in conflicts[:4]))
    if dup_groups:
        hints.append("画面几乎重复的镜头："
                     + "、".join("/".join(f"#{i}" for i in g) for g in dup_groups[:3]) + "（选一个用）")
    if overlay.get("嫌疑") == "有":
        hints.append(f"画面 {overlay.get('位置')} 疑有固定压字/水印：别当封面，必要时遮罩")
    for p in profiles[:6]:
        u = p.get("理解") or {}
        if u.get("主体"):
            hints.append(f"#{p['shot']} {p['t0']:.1f}-{p['t1']:.1f}s：{u.get('主体')}"
                         + (f"（{u.get('动作')}）" if u.get("动作") else ""))
    return hints


def load(video: str | Path, **kw) -> dict:
    return build(video, **kw)


def shot_at(profile: dict, t: float) -> dict | None:
    for p in profile.get("shots", []):
        if p["t0"] - 1e-6 <= t < p["t1"]:
            return p
    return None


def describe(profile: dict) -> list[str]:
    lines = [f"镜头档案 · {Path(profile['video']).name} · {profile.get('duration')}s · "
             f"{len(profile.get('shots', []))} 个镜头"
             + ("（缓存）" if profile.get("cached") else "")]
    for p in profile.get("shots", []):
        u = p.get("理解") or {}
        lines.append(f"  #{p['shot']} {p['t0']:6.2f}->{p['t1']:6.2f} "
                     f"{p['指标'].get('grade', '?'):<3} 可辨认 {u.get('可辨认度', '?')} "
                     f"美学 {(p.get('美学') or {}).get('评分', '?')} | "
                     f"{str(u.get('主体', '未识别'))[:20]} {str(u.get('景别', ''))}")
    for hint in profile.get("summary", {}).get("hints", []):
        lines.append("  · " + hint)
    return lines
