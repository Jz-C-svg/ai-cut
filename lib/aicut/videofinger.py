"""视频剪辑指纹（v3.7 模板化剪辑引擎 · 模块1）。

把任意视频（用户素材或参考视频）聚合成一份**结构化剪辑指纹**：
    video_type / structure（钩子-主体-结尾）/ rhythm（镜头节奏）/ shot_pattern（镜头模式）
    / subtitle（字幕密度）/ visual_density / audio（人声/配乐）

设计原则：
  · **不重跑模型**——只复用已有的 semantic_profile / shotprof / whisper 转写三份档案做聚合；
  · 纯函数 from_profiles 负责装配，便于自测；
  · build() 负责文件探测、缺失时按需调用底层管线、缓存到 work/fingerprint-<name>.json；
  · 全程中文，只用标准库，不引入新依赖。

指纹是"剪辑结构特征"，不是画面素材：提取的是节奏/骨架/模式，绝不搬运画面。
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

from .paths import WORK, TRANSCRIPTS, fingerprint

# ---- 封闭枚举 --------------------------------------------------------------
VIDEO_TYPES = (
    "koubo-zhishi", "duibi", "jiaocheng", "qingxu-gushi",
    "kadian", "vlog", "fangtan", "other",
)
HOOK_TYPES = ("提问式", "结论式", "悬念式", "痛点式", "场景式", "other")
ENDING_TYPES = ("CTA引导", "总结升华", "开放式", "彩蛋", "other")
BODY_RHYTHMS = ("递进", "并列", "对比", "故事", "other")

# 适合结构（semantic_profile 产出） -> video_type 的直接映射
_STRUCT_TO_TYPE = {
    "koubo-zhishi": "koubo-zhishi",
    "duibi": "duibi",
    "jiaocheng": "jiaocheng",
    "qingxu-gushi": "qingxu-gushi",
}

# 钩子类型关键词（按优先级命中即返回）
_HOOK_KEYWORDS = [
    ("提问式", ("吗？", "吗?", "呢？", "呢?", "？", "?", "为什么", "怎么", "如何")),
    ("痛点式", ("别", "不要", "小心", "警惕", "注意", "害怕", "担心", "误区", "踩坑", "错了", "千万别")),
    ("悬念式", ("竟然", "居然", "你不知道", "没想到", "真相", "秘密", "背后", "原来")),
    ("结论式", ("就是", "其实就是", "一定", "必须", "答案", "记住", "结论", "重点", "第一点")),
    ("场景式", ("大家好", "今天", "我是", "欢迎", "跟大家", "姐妹们", "宝妈们")),
]

# 结尾类型关键词
_ENDING_KEYWORDS = [
    ("CTA引导", ("关注", "收藏", "点赞", "评论", "私信", "留言", "转发", "记得", "双击", "点个")),
    ("开放式", ("？", "?", "你觉得", "你怎么看", "评论区说说", "你会怎么做")),
    ("彩蛋", ("哦对了", "最后说", "其实还有", "补充一句", "另外", "还有一个")),
    ("总结升华", ("总之", "所以", "希望", "愿", "祝", "总结", "记住", "加油", "陪伴", "爱你")),
]

# 主体节奏关键词
_RHYTHM_KEYWORDS = [
    ("对比", ("对比", "区别", "不同", "vs", "还是", "而另一个", "另一方面")),
    ("并列", ("第一", "第二", "第三", "首先", "其次", "然后", "最后一点", "另外", "还有")),
    ("递进", ("因为", "所以", "导致", "结果", "从而", "于是", "接下来", "接着")),
]


# ---------------------------------------------------------------- 纯逻辑小工具
def _text_of(segments: list[dict]) -> str:
    return "".join(str(s.get("text", "")) for s in segments)


def _clip_text(segments: list[dict], t0: float, t1: float) -> str:
    """取落在 [t0, t1] 时间窗内的转写文本拼接。"""
    out = []
    for s in segments or []:
        try:
            st, en = float(s.get("start", 0)), float(s.get("end", 0))
        except (TypeError, ValueError):
            continue
        if en < t0 or st > t1:
            continue
        out.append(str(s.get("text", "")))
    return "".join(out)


def _classify_by_keywords(text: str, table) -> str:
    for label, kws in table:
        for kw in kws:
            if kw in text:
                return label
    return "other"


# ---------------------------------------------------------------- 对外纯函数
def classify_video_type(semantic: dict | None, transcript_text: str) -> str:
    """基于语义档案的"适合结构"+ 转写关键词判断视频类型。"""
    semantic = semantic or {}
    struct = str(semantic.get("适合结构") or "").strip()
    if struct in _STRUCT_TO_TYPE:
        return _STRUCT_TO_TYPE[struct]
    text = transcript_text or ""
    if any(k in text for k in ("对比", "区别", "哪个好", "vs", "还是选")):
        return "duibi"
    if any(k in text for k in ("第一步", "第二步", "跟着做", "教程", "怎么做", "手把手")):
        return "jiaocheng"
    if any(k in text for k in ("那天", "后来", "以前", "故事", "记得", "小时候")):
        return "qingxu-gushi"
    if any(k in text for k in ("采访", "提问", "问：", "答：", "你觉得")):
        return "fangtan"
    if any(k in text for k in ("vlog", "日常", "一天", "plog")):
        return "vlog"
    return "koubo-zhishi"  # 本项目主战场，兜底


def detect_hook(semantic: dict | None, segments: list[dict], window: float = 3.0) -> dict:
    """从开头 window 秒台词判断钩子类型。"""
    semantic = semantic or {}
    head = _clip_text(segments, 0.0, window).strip()
    # 语义档案的高光候选如果落在前 1.5 秒，也参与判断
    type_ = _classify_by_keywords(head, _HOOK_KEYWORDS) if head else "other"
    if type_ == "other" and head:
        type_ = "结论式"  # 开头直接陈述内容（"这就是黄疸"）默认算结论式
    return {"hook_type": type_, "hook_text": head[:40]}


def detect_ending(semantic: dict | None, segments: list[dict],
                 duration: float, window: float = 3.0) -> dict:
    """从结尾 window 秒台词判断结尾类型。"""
    semantic = semantic or {}
    tail = _clip_text(segments, max(0.0, duration - window), duration).strip()
    type_ = _classify_by_keywords(tail, _ENDING_KEYWORDS) if tail else "other"
    return {"ending_type": type_, "ending_text": tail[:40]}


def _detect_body_rhythm(semantic: dict, transcript_text: str) -> str:
    pts = semantic.get("关键信息点") or []
    summary = semantic.get("叙事摘要") or []
    text = transcript_text
    label = _classify_by_keywords(text, _RHYTHM_KEYWORDS)
    if label != "other":
        return label
    # 没有关键词时按信息点数量推断：>=3 条知识型默认递进
    if len(pts) >= 2 or len(summary) >= 2:
        return "递进"
    return "other"


def _rhythm_from_shots(shots: list[dict]) -> dict:
    durs = [float(s.get("len", 0.0) or 0.0) for s in shots or []]
    durs = [d for d in durs if d > 0.1]
    if not durs:
        return {"shot_durations": [], "avg_shot": 0.0, "shot_std": 0.0,
                "pace": "other", "shot_count": 0}
    avg = statistics.fmean(durs)
    std = statistics.pstdev(durs) if len(durs) > 1 else 0.0
    if avg < 2.5:
        pace = "快剪"
    elif avg <= 5.0:
        pace = "中速"
    else:
        pace = "舒缓"
    return {"shot_durations": [round(d, 2) for d in durs],
            "avg_shot": round(avg, 2), "shot_std": round(std, 2),
            "pace": pace, "shot_count": len(durs)}


def _shot_pattern(shots: list[dict]) -> dict:
    sizes = [str(s.get("shot_size") or "medium") for s in shots or []]
    motions = [str(s.get("shot_motion") or "static") for s in shots or []]
    dom_size = Counter(sizes).most_common(1)[0][0] if sizes else "medium"
    dom_motion = Counter(motions).most_common(1)[0][0] if motions else "static"
    return {"size_sequence": sizes, "motion_sequence": motions,
            "dominant_size": dom_size, "dominant_motion": dom_motion}


def _subtitle_stats(segments: list[dict], duration: float) -> dict:
    if not segments or duration <= 0:
        return {"density": 0.0, "avg_chars_per_line": 0.0, "line_count": 0}
    speech = sum(max(0.0, float(s.get("end", 0)) - float(s.get("start", 0))) for s in segments)
    chars = [len(str(s.get("text", "")).strip()) for s in segments]
    chars = [c for c in chars if c > 0]
    avg_chars = statistics.fmean(chars) if chars else 0.0
    return {"density": round(min(1.0, speech / duration), 3),
            "avg_chars_per_line": round(avg_chars, 1),
            "line_count": len(segments)}


def _audio_stats(segments: list[dict], duration: float) -> dict:
    """没有 BGM/配音信息源时按转写推断：有声段占比即 speech_ratio；
    has_voiceover 默认为 True（口播类素材都有人声），has_bgm 未知时留 None。"""
    speech = sum(max(0.0, float(s.get("end", 0)) - float(s.get("start", 0))) for s in segments or [])
    ratio = round(min(1.0, speech / duration), 3) if duration > 0 else 0.0
    return {"has_voiceover": bool(segments),
            "has_bgm": None,
            "speech_ratio": ratio}


def _visual_density(shots: list[dict]) -> float:
    """视觉密度的粗估：有明显运动（非 static/locked）的镜头占比。"""
    shots = shots or []
    if not shots:
        return 0.0
    moving = sum(1 for s in shots if str(s.get("shot_motion")) not in ("static", "locked", ""))
    return round(moving / len(shots), 3)


def from_profiles(*, semantic: dict | None = None, shotprof: dict | None = None,
                  transcript: dict | None = None, video: str | None = None,
                  duration: float | None = None) -> dict:
    """纯函数：从已有三份档案聚合出剪辑指纹（不碰文件、不跑模型）。

    参数：
      semantic  work/semantic-<name>.json 内容
      shotprof  work/shotprof-<name>.json 内容
      transcript 02-transcript/<name>.json 内容（{segments:[...]}）
      video     视频路径（仅记录用）
      duration  视频秒数；不传则从 transcript.duration / shotprof.duration 推断
    """
    semantic = semantic or {}
    shotprof = shotprof or {}
    transcript = transcript or {}
    segments = transcript.get("segments") or []
    shots = shotprof.get("shots") or []

    if duration is None:
        duration = float(shotprof.get("duration") or transcript.get("duration") or 0.0)
    duration = round(float(duration or 0.0), 3)

    transcript_text = _text_of(segments)
    vtype = classify_video_type(semantic, transcript_text)
    hook = detect_hook(semantic, segments, window=3.0)
    ending = detect_ending(semantic, segments, duration, window=3.0)
    rhythm = _rhythm_from_shots(shots)
    pattern = _shot_pattern(shots)
    sub = _subtitle_stats(segments, duration)
    audio = _audio_stats(segments, duration)

    return {
        "video": video or "",
        "fingerprint": "",
        "duration": duration,
        "video_type": vtype,
        "semantic": {
            "主角": semantic.get("主角", ""),
            "场景": semantic.get("场景", ""),
            "叙事摘要": list(semantic.get("叙事摘要") or []),
            "关键信息点": list(semantic.get("关键信息点") or []),
            "情绪基调": semantic.get("情绪基调", ""),
            "适合结构": semantic.get("适合结构", ""),
            "高光候选": list(semantic.get("高光候选") or []),
        },
        "structure": {
            "hook_type": hook["hook_type"],
            "hook_text": hook["hook_text"],
            "body_rhythm": _detect_body_rhythm(semantic, transcript_text),
            "ending_type": ending["ending_type"],
            "ending_text": ending["ending_text"],
        },
        "rhythm": rhythm,
        "shot_pattern": pattern,
        "subtitle": sub,
        "visual_density": _visual_density(shots),
        "audio": audio,
    }


# ---------------------------------------------------------------- 文件 I/O
def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}


def build(video: str | Path, *, semantic_path: str | Path | None = None,
          shotprof_path: str | Path | None = None,
          transcript_path: str | Path | None = None, force: bool = False) -> dict:
    """主入口：自动探测三份档案（work/ 与 02-transcript/），缺什么补什么，聚合指纹并缓存。

    缓存：work/fingerprint-<stem>.json；缓存键=视频文件指纹（paths.fingerprint）。
    """
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(video)
    fp_str = fingerprint(video)
    out_file = WORK / f"fingerprint-{video.stem}.json"

    if not force and out_file.exists():
        cached = _load_json(out_file)
        if cached.get("fingerprint") == fp_str:
            cached["cached"] = True
            cached["file"] = str(out_file)
            return cached

    sem_path = Path(semantic_path) if semantic_path else WORK / f"semantic-{video.stem}.json"
    sp_path = Path(shotprof_path) if shotprof_path else WORK / f"shotprof-{video.stem}.json"
    tp_path = Path(transcript_path) if transcript_path else TRANSCRIPTS / f"{video.stem}.json"

    semantic_doc = _load_json(sem_path)
    shotprof_doc = _load_json(sp_path)
    transcript_doc = _load_json(tp_path)

    # 缺失档案按需调用底层管线（只补缺失的，已有的不重跑）
    if not semantic_doc:
        try:
            from . import semantic_profile  # 延迟导入避免循环依赖
            semantic_doc = semantic_profile.build(video, transcript_path=str(tp_path) if tp_path.exists() else None)
        except Exception as exc:  # noqa: BLE001
            print(f"[videofinger] 语义档案生成失败({type(exc).__name__}: {exc})，按空档案继续")
            semantic_doc = {}
    if not shotprof_doc:
        try:
            from . import shotprof  # 延迟导入
            shotprof_doc = shotprof.build(video)
        except Exception as exc:  # noqa: BLE001
            print(f"[videofinger] 镜头档案生成失败({type(exc).__name__}: {exc})，按空档案继续")
            shotprof_doc = {}
    if not transcript_doc:
        # 转写必须走 whisper，成本高且耗时；缺失时给出空 segments 并标注
        print(f"[videofinger] 未找到转写 {tp_path}，请先跑 scripts/transcribe.py；本次按空转写继续")

    result = from_profiles(semantic=semantic_doc, shotprof=shotprof_doc,
                           transcript=transcript_doc, video=str(video))
    result["fingerprint"] = fp_str
    result["file"] = str(out_file)
    save(result, out_file)
    return result


def save(fp: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(fp, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load(path: str | Path) -> dict:
    return _load_json(Path(path))
