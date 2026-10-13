r"""视频级"内容语义档案"（v3.4 理解层核心新模块）。

和 shotprof.py 的分工：
  shotprof.py       镜头级：每个镜头看清"是什么、能不能用"（逐镜头 VLM）
  semantic_profile  视频级：整条片子在"讲什么、该怎么剪"（一次 VLM 综合）

思路参考 HIVE（arXiv 2507.02790）三任务分解的本地版：
  均匀取 3-5 个代表帧 + whisper 逐句转写文本 -> 本地 Ollama（gemma4，支持多图）
  一次调用，输出整条视频的：主角 / 场景 / 叙事摘要 / 关键信息点 / 情绪基调 /
  适合结构 / 高光候选 / 叙事时间轴。

硬约束：只用本地免费模型（Ollama），不引入需注册/付费的 API；FFmpeg 只做分析抽帧。
缓存：fingerprint 为键，同时写 memory.sqlite 与 work/semantic-<素材名>.json。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from . import llm, shotprof, vision
from .memory import open_memory
from .paths import WORK, TRANSCRIPTS, fingerprint

# 适合结构枚举（封闭槽位，模型只能从这里选）
STRUCTURES = ("koubo-zhishi", "duibi", "jiaocheng", "qingxu-gushi")
# 字段完整率统计所依赖的必填字段
REQUIRED_FIELDS = ("主角", "场景", "叙事摘要", "关键信息点", "情绪基调", "适合结构")

# 一次调用要求模型输出的 JSON schema
SEMANTIC_PROMPT = """你是资深短视频内容分析师。下面给你这段视频按时间先后排列的若干代表帧，
以及整段口播的逐句转写文本。请综合判断，只输出一个 JSON 对象，不要解释、不要代码块：
{
 "主角": "画面里反复出现的人或主体（一句话，20字内）",
 "场景": "在哪里/什么环境（一句话）",
 "叙事摘要": ["第1个意思", "第2个意思", "第3个意思"],
 "关键信息点": ["这条视频最该被记住的知识点或数字，1到3条"],
 "情绪基调": "例如 专业/平静、温暖、紧张、开心",
 "适合结构": "只能填 koubo-zhishi（口播知识）/ duibi（对比）/ jiaocheng（教程）/ qingxu-gushi（情绪故事） 之一",
 "高光候选": [{"t": 秒数, "理由": "为什么这段值得当高光"}]
}
其中 高光候选 的 t 必须取转写里出现过的秒数（0 到视频总时长之间），最多给 3 个。"""


# ---------------------------------------------------------------- 纯逻辑小工具
def _field_filled(value) -> bool:
    """必填字段是否真有内容：空串/None/空列表都算没填。"""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    return True


def _load_transcript(video: Path, transcript_path: str | Path | None) -> tuple[list[dict], str]:
    """读取 whisper 转写。优先用显式传入，否则自动探测 02-transcript/<素材名>.json。
    返回 (segments[{start,end,text}], 实际用到的文件路径)。"""
    if transcript_path:
        p = Path(transcript_path)
    else:
        p = TRANSCRIPTS / f"{video.stem}.json"
    if not p.exists():
        return [], str(p)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return [], str(p)
    out: list[dict] = []
    for s in data.get("segments") or []:
        if isinstance(s, dict) and s.get("text"):
            out.append({
                "start": float(s.get("start", 0.0) or 0.0),
                "end": float(s.get("end", 0.0) or 0.0),
                "text": str(s["text"]).strip(),
            })
    return out, str(p)


def _rep_times(duration: float, max_frames: int) -> list[float]:
    """均匀分布取代表帧时间点：把时长均分成 max_frames 段，取每段中点。"""
    n = max(3, min(max_frames, 5))  # 代表帧固定 3-5 个
    if duration <= 0:
        return [1.0]
    step = duration / n
    return [round(max(0.2, step * (i + 0.5)), 2) for i in range(n)]


def _vision_json_multi(images: list[Path], prompt: str, *, use_cloud: bool) -> dict:
    """多图视觉调用：直接走 Ollama 的 images 列表（gemma4 支持多图）。
    多图失败时回退到单图 vision_json（其内部自带 ollama->deepseek 回退）。"""
    b64s = [llm._b64(p) for p in images if Path(p).exists()]
    if not b64s:
        return {}
    model = llm.get_vision_model()
    llm.assert_local_vision_healthy(model)
    try:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt, "images": b64s}],
            "stream": False,
            "keep_alive": "30m",
            "format": "json",
            "think": False,  # 关闭思考，避免吃光 num_predict 导致空返回
            "options": llm._ollama_options(0.3),
        }
        data = llm._post(llm.OLLAMA_URL, payload, timeout=600)
        if data.get('done_reason') == 'length':
            raise ValueError('语义分析输出被截断')
        out = llm.extract_json(data.get("message", {}).get("content", ""))
        out["_provider"] = "ollama"
        out["_model"] = model
        return out
    except Exception:  # noqa: BLE001
        provider = "auto" if use_cloud else "ollama"
        return llm.vision_json(images[0], prompt, provider=provider, model=model)


def _heuristic_highlights(segments: list[dict], duration: float, k: int = 3) -> list[dict]:
    """VLM 没给高光时的兜底：按"句长 + 数字个数"估信息密度，取前 k 个。"""
    scored = []
    for seg in segments:
        text = seg.get("text", "")
        score = len(text) + 8 * sum(ch.isdigit() for ch in text)
        scored.append((score, seg))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for _, seg in scored[:k]:
        t = (float(seg["start"]) + float(seg["end"])) / 2
        out.append({"t": round(min(max(t, 0.0), duration), 2),
                    "理由": "转写信息密度较高（句长/含数字）"})
    out.sort(key=lambda x: x["t"])
    return out


def _build_timeline(segments: list[dict], shots: list[dict]) -> list[dict]:
    """任务4：把 whisper 句级时间戳与 shotprof 镜头时间轴对齐。
    对每个转写句，取覆盖它时间中点的那个镜头的画面描述作为"画面"字段。"""
    timeline: list[dict] = []
    for seg in segments:
        t0 = float(seg.get("start", 0.0))
        t1 = float(seg.get("end", t0))
        mid = (t0 + t1) / 2.0
        pic = ""
        for s in shots:
            s0 = float(s.get("t0", 0.0)); s1 = float(s.get("t1", 0.0))
            if s0 - 1e-6 <= mid < s1:
                pic = str(s.get("content") or (s.get("理解") or {}).get("主体") or "")
                break
        timeline.append({
            "t0": round(t0, 2),
            "t1": round(t1, 2),
            "台词": seg.get("text", ""),
            "画面": pic or "（无镜头档案）",
        })
    return timeline


def _assemble(*, video: Path, fp: str, duration: float, raw: dict, segments: list[dict],
              shots: list[dict], frame_times: list[float], source: str,
              transcript_file: str) -> dict:
    """把 VLM 原始输出 + 转写 + 镜头档案装配成规范的语义档案 JSON（纯函数，便于自测）。"""
    raw = raw or {}

    def _list(v):
        if isinstance(v, list):
            return [str(x) for x in v if str(x).strip()]
        return [str(v)] if v and str(v).strip() else []

    zhujue = str(raw.get("主角") or "").strip() or "待补"
    changjing = str(raw.get("场景") or "").strip() or "待补"
    emotion = str(raw.get("情绪基调") or "").strip() or "专业/平静"
    structure = str(raw.get("适合结构") or "").strip()
    if structure not in STRUCTURES:
        structure = "koubo-zhishi"  # 口播知识是本项目最常见的兜底

    # 高光候选：信任 VLM 给的、落在时长范围内的；否则用启发式兜底
    hl_out: list[dict] = []
    for h in raw.get("高光候选") or []:
        if not isinstance(h, dict) or h.get("t") is None:
            continue
        try:
            t = float(h["t"])
        except (TypeError, ValueError):
            continue
        if 0 <= t <= duration:
            hl_out.append({"t": round(t, 2), "理由": str(h.get("理由", ""))})
    if not hl_out:
        hl_out = _heuristic_highlights(segments, duration)
    hl_out = hl_out[:3]

    timeline = _build_timeline(segments, shots)

    # 字段完整率：6 个必填字段里实际填了几个
    filled = sum(1 for k in REQUIRED_FIELDS if _field_filled(raw.get(k)))
    completeness = round(filled / len(REQUIRED_FIELDS), 3)

    return {
        "video": str(video),
        "fingerprint": fp,
        "duration": round(duration, 3),
        "主角": zhujue,
        "场景": changjing,
        "叙事摘要": _list(raw.get("叙事摘要")),
        "关键信息点": _list(raw.get("关键信息点")),
        "情绪基调": emotion,
        "适合结构": structure,
        "高光候选": hl_out,
        "叙事时间轴": timeline,
        "代表帧时间点": [round(t, 2) for t in frame_times],
        "字段完整率": completeness,
        "来源": source or "ollama",
        "transcript": transcript_file,
        "cached": False,
    }


# ---------------------------------------------------------------- 对外主接口
def build(video: str | Path, *, transcript_path: str | Path | None = None,
          use_cloud: bool = False, force: bool = False, max_frames: int = 4) -> dict:
    """生成（或读缓存）整条视频的内容语义档案。

    参数：
      video          素材路径（建议用 work/normalized/*.mp4）
      transcript_path  转写 json；不传则自动探测 02-transcript/<素材名>.json
      use_cloud      True 时允许云端视觉回退；默认全程使用本机视觉通道
      force          True 忽略缓存重算
      max_frames     代表帧数量（内部固定夹到 3-5）
    """
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(video)
    fp = fingerprint(video)
    mem = open_memory()
    out_file = WORK / f"semantic-{video.stem}.json"

    # ---- 缓存：先读 work 文件，再读 memory.sqlite ----
    if not force:
        if out_file.exists():
            try:
                loaded = json.loads(out_file.read_text(encoding="utf-8-sig"))
                if loaded.get("fingerprint") == fp:
                    loaded["cached"] = True
                    loaded["file"] = str(out_file)
                    return loaded
            except Exception:  # noqa: BLE001
                pass
        cached = mem.get_visual(f"semantic:{fp}")
        if cached and cached.get("fingerprint") == fp:
            cached["cached"] = True
            return cached

    # ---- 读转写、定时长 ----
    segments, transcript_file = _load_transcript(video, transcript_path)
    duration = vision.duration_of(video) or 0.0
    if not duration:  # 转写里有时长就用它兜底
        try:
            duration = float(json.loads(Path(transcript_file).read_text(encoding="utf-8-sig")).get("duration", 0.0)) if transcript_file else 0.0
        except Exception:  # noqa: BLE001
            duration = 0.0

    # ---- 抽代表帧 ----
    times = _rep_times(duration, max_frames)
    tmp = Path(tempfile.mkdtemp(prefix="aicut-semprof-"))
    frame_paths: list[Path] = []
    try:
        for t in times:
            p = tmp / f"rep_{t:.2f}.jpg"
            if shotprof.extract_frame(video, t, p):
                frame_paths.append(p)

        # ---- 镜头档案：没有就自动生成（供时间轴对齐"画面"用）----
        shots: list[dict] = []
        try:
            sp = shotprof.build(video, use_cloud=use_cloud, force=False)
            shots = sp.get("shots", []) or []
        except Exception:  # noqa: BLE001
            shots = []

        # ---- 拼 prompt：代表帧 + 逐句转写，一次调用 ----
        transcript_text = "\n".join(
            f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)
        prompt = SEMANTIC_PROMPT + "\n\n【逐句转写】\n" + (transcript_text or "（无转写）")
        raw = _vision_json_multi(frame_paths, prompt, use_cloud=use_cloud)
        source = raw.get("_provider", "ollama") if isinstance(raw, dict) else "ollama"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    result = _assemble(video=video, fp=fp, duration=duration, raw=raw, segments=segments,
                      shots=shots, frame_times=times, source=source,
                      transcript_file=transcript_file)

    # ---- 写双缓存 ----
    mem.cache_visual(f"semantic:{fp}", str(video), result)
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    result["file"] = str(out_file)
    return result


def load(video: str | Path, **kw) -> dict:
    """同 build，主要用于"读缓存"语义。"""
    return build(video, **kw)


# ---------------------------------------------------------------- GLM 深度理解通道
def deep_build(video: str | Path, *, transcript_path: str | Path | None = None,
               force: bool = False, timeout: int = 900) -> dict:
    """GLM-4.6V-Flash 原生视频理解版语义档案（理解质量优于 gemma4 多图）。

    用 glm_deep.analyze_video 对整个素材做一次原生视频理解（含时序动作），
    按 SEMANTIC_PROMPT 的 schema 输出，再走 _assemble 装配与双缓存。
    若 GLM 不可用/超时，明确失败；不会把文本模型结果当作视觉理解。

    返回结构与 build() 一致，额外含 来源='glm-local-deep'。
    """
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(video)
    fp = fingerprint(video)
    mem = open_memory()
    out_file = WORK / f"semantic-glm-{video.stem}.json"
    if not force:
        if out_file.exists():
            try:
                loaded = json.loads(out_file.read_text(encoding="utf-8-sig"))
                if loaded.get("fingerprint") == fp:
                    loaded["cached"] = True
                    loaded["file"] = str(out_file)
                    return loaded
            except Exception:
                pass
        cached = mem.get_visual(f"semantic-glm:{fp}")
        if cached and cached.get("fingerprint") == fp:
            cached["cached"] = True
            return cached

    try:
        from . import glm_deep
        if not glm_deep.is_available():
            raise RuntimeError("内置视觉模型不可用")
        segments, transcript_file = _load_transcript(video, transcript_path)
        duration = vision.duration_of(video) or 0.0
        transcript_text = "\n".join(
            f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)
        prompt = SEMANTIC_PROMPT + "\n\n【逐句转写】\n" + (transcript_text or "（无转写）")
        from . import bundled_vision
        raw = bundled_vision.analyze_video(video, prompt=prompt, max_tokens=900,
                                           temperature=0.2, timeout=timeout)
        raw = bundled_vision.normalize_video_result(raw or {})
        if not raw or raw.get("error") or not raw.get("主体") and not raw.get("主角"):
            raise RuntimeError("内置视觉模型返回的主体字段不完整")
        raw.setdefault("主角", raw.get("主体", ""))
        raw.setdefault("场景", raw.get("场景", "待补"))
        raw.setdefault("叙事摘要", [raw.get("内容", "")] if raw.get("内容") else [])
        if not raw.get("关键信息点"):
            raw["关键信息点"] = [
                s["text"] for s in segments if len(s.get("text", "")) >= 6
            ][:3]
        raw.setdefault("情绪基调", "专业/平静")
        raw.setdefault("适合结构", "koubo-zhishi")
        raw.setdefault("高光候选", [])
        raw.setdefault("_provider", "glm-local-deep")
        source = "glm-local-deep"
        result = _assemble(video=video, fp=fp, duration=duration, raw=raw,
                           segments=segments, shots=[], frame_times=[],
                           source=source, transcript_file=transcript_file)
    except Exception as exc:
        error_file = out_file.with_suffix(".error.json")
        try:
            error_file.write_text(json.dumps({
                "video": str(video),
                "provider": "glm-local-deep",
                "error": str(exc),
                "next_action": "检查内置视觉模型日志后重试；不要自动降级到文本模型",
            }, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
        raise RuntimeError(
            f"内置视觉模型未能生成可执行语义档案；诊断文件: {error_file}"
        ) from exc

    try:
        mem.cache_visual(f"semantic-glm:{fp}", str(video), result)
        out_file.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    except Exception:
        pass
    result["file"] = str(out_file)
    return result
