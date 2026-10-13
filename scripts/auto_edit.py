#!/usr/bin/env python3
r"""AI 代剪辑：给素材和一个风格，自动完成剪辑并生成剪映草稿。

做四件事：
  1. 按风格的镜头时长切镜头（以句子为自然边界，过长拆分、过短合并）
  2. 按风格加转场、开场特效、滤镜
  3. 按风格排字幕（字体/字号/描边/位置由样式预设，行宽按风格断行）
  4. 配 BGM、写封面文案与开场动画，输出决策单并生成剪映草稿

用法:
    python auto_edit.py --video <素材或已标准化视频> --style koubo-zhishi [--transcript 02-transcript\x.json]
                        [--name 成片名] [--cover "封面文案"] [--cta "行动指令"] [--bgm 04-assets\bgm\a.mp3] [--publish]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

import cleanup  # noqa: E402
import understand  # noqa: E402

STYLES = ROOT / "content-library" / "styles.json"
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac"}

DEFAULT_CTA = {
    "收藏": "收藏这条，需要的时候翻出来看",
    "到店": "私信预约参观",
    "预约": "私信预约参观",
    "成交": "私信了解详情",
    "涨粉": "关注看后续",
    "信任": "关注看后续",
    "共鸣": "评论区说说你的经历",
    "评论": "评论区聊聊你的看法",
    "传播": "转发给需要的朋友",
    "播放": "关注看更多",
    "决策参考": "收藏这份对比",
}

# v3.6：每个风格至少 1 条定制 CTA（按风格 id 优先，其次按目标回退 DEFAULT_CTA）
STYLE_CTA = {
    "koubo-zhishi": "收藏这条，月子里用得上",
    "kongjian-zhanshi": "私信预约参观房型",
    "qingxu-gushi": "评论区说说你的经历",
    "duibi-ceping": "收藏这份对比，选房不踩坑",
    "kadian-jinji": "关注看下一期",
    "wenzi-kuaishan": "收藏这组清单，照着做",
    "jiating-richang": "关注看我们的日常",
    "zhuanti-pandian": "收藏这条，几个技巧一次记牢",
    "jiaocheng-buzhou": "收藏跟着做，一步不落",
    "fangtan-duitan": "评论区你想提问什么",
    "zhiyu-shuhuan": "关注陪你慢慢度过月子期",
    "xuanchuan-pian": "私信预约，到店看看真实环境",
}


def load_style(sid: str) -> dict:
    """优先读 per-style 完整卡（content-library/styles/<id>.json），不存在则回退索引内旧格式。"""
    data = json.loads(STYLES.read_text(encoding="utf-8-sig"))
    for s in data["styles"]:
        if s["id"] == sid:
            rel = s.get("文件")
            if rel:
                p = STYLES.parent / rel
                if p.exists():
                    return json.loads(p.read_text(encoding="utf-8"))
            return s
    raise KeyError(f"没有这个风格: {sid}（可用: " + ", ".join(x["id"] for x in data["styles"]) + ")")


def list_styles() -> list[dict]:
    """从 styles.json 索引列出全部可用风格（id/名称/目标/适用）。"""
    data = json.loads(STYLES.read_text(encoding="utf-8-sig"))
    return [{"id": s["id"],
             "名称": s.get("名称", ""),
             "目标": s.get("目标", []),
             "适用": s.get("适用", [])} for s in data["styles"]]


def _auto_style_inputs(video: Path) -> tuple[dict | None, dict | None, str]:
    """--auto-style 时探测语义档案 / shotprof 摘要 / 转写文本（与 --semantic 同一路径约定）。"""
    sem_path = ROOT / "work" / f"semantic-{video.stem}.json"
    if sem_path.exists():
        try:
            return json.loads(sem_path.read_text(encoding="utf-8")), None, ""
        except Exception:  # noqa: BLE001
            pass
    shotprof_summary = None
    sp_path = ROOT / "work" / f"shotprof-{video.stem}.json"
    if sp_path.exists():
        try:
            shotprof_summary = (json.loads(sp_path.read_text(encoding="utf-8")).get("summary")) or {}
        except Exception:  # noqa: BLE001
            shotprof_summary = None
    transcript_text = ""
    tp_path = ROOT / "02-transcript" / f"{video.stem}.json"
    if tp_path.exists():
        try:
            segs = json.loads(tp_path.read_text(encoding="utf-8")).get("segments") or []
            transcript_text = " ".join(str(x.get("text", "")) for x in segs)
        except Exception:  # noqa: BLE001
            transcript_text = ""
    return None, shotprof_summary, transcript_text


def _cover_impact(s: str) -> int:
    """给一句关键信息点打冲击力分：含数字/结果/痛点词加分。"""
    s = str(s)
    score = sum(1 for ch in s if ch.isdigit()) * 3
    for kw in ("最", "别", "注意", "不要", "一定", "只有", "才", "如何", "怎么",
               "！", "!", "吗", "真相", "误区"):
        if kw in s:
            score += 2
    return score


def _smart_cover(semantic_doc: dict | None, cover_hint: str, first_text: str) -> str:
    """未指定 --cover 时智能生成：关键信息点最有冲击力的一条 -> 讲了什么 -> 首句。"""
    if semantic_doc:
        pts = [str(p) for p in (semantic_doc.get("关键信息点") or []) if str(p).strip()]
        if pts:
            best = sorted(pts, key=_cover_impact, reverse=True)[0]
            if best.strip():
                return best.strip()[:14]
        what = semantic_doc.get("讲了什么")
        if not what:
            narr = semantic_doc.get("叙事摘要") or []
            what = narr[0] if narr else ""
        if what and str(what).strip():
            return str(what).strip()[:14]
    if cover_hint:
        return str(cover_hint)[:14]
    return first_text[:14]


def _ollama_cover(semantic_doc: dict | None, source_text: str = '') -> str:
    """Ollama 可用时根据关键信息点生成 ≤14 字封面文案；失败返回空串。"""
    if not semantic_doc:
        return ""
    pts = semantic_doc.get("关键信息点") or []
    if not pts:
        return ""
    try:
        from aicut import llm  # noqa: E402
        prompt = ("根据下面的视频信息选一条封面文案，不超过14字。"
                   "有原始台词时只能摘取其中连续原话，不能新增数字、步骤、功效或承诺。"
                   "只输出文案本身。\n原始台词：" + source_text
                   + "\n关键信息点：" + "；".join(str(p) for p in pts[:5]))
        out = llm.chat([{"role": "user", "content": prompt}],
                       provider="ollama", temperature=0.4, timeout=60)
        strip_chars = "“”\"'。.，, 　"
        out = (out or "").strip().strip(strip_chars).splitlines()[0] if out else ""
        return out[:14] if out else ""
    except Exception:  # noqa: BLE001
        return ""


def grounded_cover(candidate: str, segments: list[dict]) -> tuple[str, dict]:
    """Automatic speech covers are source excerpts; model summaries are not proof."""
    texts = [str(s.get('text', '')).strip() for s in segments if str(s.get('text', '')).strip()]
    normalize = lambda text: re.sub(r'[\W_]+', '', str(text), flags=re.UNICODE).lower()
    quote = normalize(candidate)
    if not texts or (quote and any(quote in normalize(text) for text in texts)):
        return candidate, {'status': 'source_excerpt' if texts else 'requires_visual_copy_review'}
    from aicut.text_overlays import wrap_caption
    fallback = wrap_caption(texts[0], 14).splitlines()[0]
    return fallback, {'status': 'replaced_unsupported_copy', 'rejected': candidate,
                      'reason': '自动封面不是原台词摘录；禁止把模型概括作为事实依据'}


def visual_query_terms(profiles: list[dict]) -> list[str]:
    terms = []
    for profile in profiles:
        proof = profile.get('semantic_evidence') or {}
        if proof.get('status') not in {'automatic_visual_observation_unreviewed',
                                       'human_reviewed_visual_observation'}:
            continue
        shot_understanding = profile.get('理解') or {}
        for value in (profile.get('content'), shot_understanding.get('主体'),
                      shot_understanding.get('动作'), profile.get('shot_size'),
                      profile.get('shot_angle'), profile.get('shot_motion')):
            if isinstance(value, str) and value.strip():
                terms.append(value.strip())
    return terms


def ffprobe_duration(path: Path) -> float:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", str(path)],
                          capture_output=True, text=True, encoding="utf-8", errors="ignore")
    try:
        return float(proc.stdout.strip())
    except Exception:
        raise RuntimeError(f"读不到时长: {path}")


def load_segments(video: Path, explicit: str | None) -> list[dict]:
    path = Path(explicit) if explicit else ROOT / "02-transcript" / f"{video.stem}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("segments", [])


def intent_cache_matches(intent: dict | None, cache_key: str) -> bool:
    """Reuse only an intent generated for the exact current inputs and schema."""
    return bool(intent and intent.get('_learning_cache_key') == cache_key
                and not (intent.get('_meta') or {}).get('degraded'))


def _load_template(template_arg: str | None) -> dict | None:
    """--template 增量集成：加载模板 JSON（模板 ID 或文件路径）。失败返回 None。"""
    if not template_arg:
        return None
    try:
        tpath = Path(template_arg)
        if not tpath.exists():
            tpath = ROOT / "content-library" / "templates" / f"{template_arg}.json"
        if not tpath.exists():
            print(f"[模板] 找不到 {template_arg}，按无模板继续")
            return None
        return json.loads(tpath.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[模板] 加载失败({type(exc).__name__}: {exc})，按无模板继续")
        return None


def _merge_template_into_params(params: dict, template: dict) -> list[str]:
    """把模板的节奏/技术参数合并进风格 params（就地修改），返回被覆盖字段说明。"""
    notes: list[str] = []
    rhythm = (template or {}).get("rhythm", {}) or {}
    tech = (template or {}).get("techniques", {}) or {}
    if rhythm.get("min_shot"):
        params["镜头最短秒"] = float(rhythm["min_shot"]); notes.append(f"镜头最短秒={params['镜头最短秒']}")
    if rhythm.get("max_shot"):
        params["镜头最长秒"] = float(rhythm["max_shot"]); notes.append(f"镜头最长秒={params['镜头最长秒']}")
    if tech.get("subtitle_max_chars"):
        params["字幕每行最多字"] = int(tech["subtitle_max_chars"]); notes.append(f"字幕每行最多字={params['字幕每行最多字']}")
    if tech.get("keyframe_mode") and tech["keyframe_mode"] != "none":
        params["关键帧"] = tech["keyframe_mode"]; notes.append(f"关键帧={tech['keyframe_mode']}")
    if tech.get("filter"):
        params["滤镜"] = tech["filter"]; notes.append(f"滤镜={tech['filter']}")
    if tech.get("opening_effect"):
        params["开场特效"] = tech["opening_effect"]; notes.append(f"开场特效={tech['opening_effect']}")
    if tech.get("transition") == "硬切":
        params["转场调用名"] = None; notes.append("转场=硬切")
    return notes


def plan_shots(segments: list[dict], short_s: float, long_s: float, total: float) -> list[list[float]]:
    """按句子边界切镜头；过长拆分、过短合并，并补足首尾。"""
    shots: list[list[float]] = []
    if segments:
        for seg in segments:
            s, e = float(seg["start"]), float(seg["end"])
            dur = e - s
            if dur <= long_s:
                shots.append([s, e])
            else:
                n = max(2, math.ceil(dur / long_s))
                step = dur / n
                shots += [[s + i * step, s + (i + 1) * step] for i in range(n)]
    else:
        target = (short_s + long_s) / 2
        n = max(1, round(total / target))
        step = total / n
        shots = [[i * step, (i + 1) * step] for i in range(n)]

    merged: list[list[float]] = []
    for s, e in shots:
        if merged and (e - s) < short_s * 0.6:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    if merged and merged[0][0] > 0.3:
        merged.insert(0, [0.0, merged[0][0]])
    if merged and total - merged[-1][1] > 0.3:
        merged.append([merged[-1][1], total])
    return merged



def rhythm_target(rhythm: dict, start: float, short_s: float, long_s: float) -> float:
    """按句子的节奏标记给出该镜头的目标长度：快->短端，慢->长端。"""
    if not rhythm:
        return (short_s + long_s) / 2
    key = min(rhythm, key=lambda k: abs(float(k) - start))
    if abs(float(key) - start) > 0.6:
        return (short_s + long_s) / 2
    return {"快": short_s * 1.05, "中": (short_s + long_s) / 2, "慢": long_s}.get(rhythm[key], (short_s + long_s) / 2)


def plan_shots_smart(segments: list[dict], short_s: float, long_s: float, total: float,
                     prefer_cuts: list[float], rhythm: dict | None = None) -> list[list[float]]:
    """识别驱动的切镜头：只能切在句子边界，优先切在语义/画面切点附近，并满足风格时长。"""
    if not segments:
        return plan_shots(segments, short_s, long_s, total)
    bounds = [float(s["start"]) for s in segments] + [total]
    prefer = set()
    for c in prefer_cuts or []:
        best = min(bounds, key=lambda b: abs(b - float(c)))
        if abs(best - float(c)) <= 1.0:
            prefer.add(round(best, 2))
    target = (short_s + long_s) / 2
    shots: list[list[float]] = []
    cur = bounds[0]
    for i, seg in enumerate(segments):
        seg_end = float(seg["end"])
        nxt = bounds[i + 1] if i + 1 < len(bounds) else total
        dur = seg_end - cur
        hit_prefer = round(nxt, 2) in prefer
        role_target = rhythm_target(rhythm or {}, float(seg["start"]), short_s, long_s)
        cut_here = (dur >= long_s) or (dur >= short_s and hit_prefer) \
            or (dur >= role_target) or (i == len(segments) - 1)
        if cut_here:
            shots.append([cur, total if i == len(segments) - 1 else seg_end])
            cur = nxt
    merged: list[list[float]] = []
    for s, e in shots:
        if merged and (e - s) < short_s * 0.6:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    if merged and merged[0][0] > 0.3:
        merged.insert(0, [0.0, merged[0][0]])
    return merged


def load_understanding(video: Path, smart: bool, explicit: str | None,
                      transcript_path: str | Path | None = None) -> dict | None:
    """读取识别结果；没有就现场生成一次。"""
    path = Path(explicit) if explicit else ROOT / "work" / f"video-understanding-{video.stem}.json"
    legacy_path = ROOT / "work" / f"understand-{video.stem}.json"
    if not explicit and not path.exists() and legacy_path.exists():
        path = legacy_path
    if path.exists():
        cached = json.loads(path.read_text(encoding='utf-8-sig'))
        fp = cached.get('素材指纹') or {}
        st = video.stat()
        current = (fp.get('大小') == st.st_size and fp.get('修改时间') == int(st.st_mtime))
        recognized = cached.get('识别方案版本') == 2 and cached.get('视觉状态') in {'成功', '部分失败', '未请求', '失败'}
        # A source-matching schema-v2 cache with intact text/signal sections
        # remains useful even when its old vision status is partial. The
        # quality-first shot-profiler below independently gates visual use.
        legacy_source_match = (not explicit and current and cached.get('识别方案版本') == 2
                               and isinstance(cached.get('信号识别'), dict)
                               and isinstance(cached.get('文本理解'), dict))
        if not (current and recognized) and not legacy_source_match:
            if explicit:
                raise ValueError('指定识别结果与素材/识别方案版本不一致，请重新生成')
            print('[AI 识别] 素材指纹、识别方案版本或视觉状态不匹配，本次重新识别')
            import uuid
            for stale in {path, legacy_path}:
                if stale.exists():
                    stale.rename(stale.with_name(stale.name + '.stale-' + uuid.uuid4().hex[:8]))
        elif legacy_source_match and not recognized:
            print('[AI 识别] 复用同源旧版文本/信号结果；视觉覆盖由本轮质量优先扫描单独核验')
    if not path.exists():
        if not smart:
            return None
        print("[AI 识别] 首次运行，正在分析素材…")
        target = legacy_path if not explicit else path
        command = [str(ROOT / "venv" / "Scripts" / "python.exe"), str(ROOT / "scripts" / "understand.py"),
                   "--video", str(video), "--out", str(target)]
        if transcript_path:
            transcript_file = Path(transcript_path)
            if not transcript_file.is_absolute():
                transcript_file = ROOT / transcript_file
            command.extend(["--transcript", str(transcript_file)])
        subprocess.run(command, cwd=str(ROOT))
        if target.exists():
            fresh = json.loads(target.read_text(encoding="utf-8"))
            if fresh.get("识别方案版本") != 2 or fresh.get("视觉状态") not in {"成功", "部分失败", "未请求", "失败"}:
                raise ValueError("本次识别没有有效结构，停止使用空结果剪辑")
        if not explicit and legacy_path.exists():
            semantic_command = [str(ROOT / "venv" / "Scripts" / "python.exe"),
                                str(ROOT / "scripts" / "video_understanding.py"),
                                "--video", str(video), "--ai-understanding", str(legacy_path),
                                "--run-signals"]
            if transcript_path:
                transcript_file = Path(transcript_path)
                if not transcript_file.is_absolute():
                    transcript_file = ROOT / transcript_file
                semantic_command.extend(["--transcript", str(transcript_file)])
            subprocess.run(semantic_command, cwd=str(ROOT))
            path = ROOT / "work" / f"video-understanding-{video.stem}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        fp = data.get("素材指纹") or {}
        st = video.stat()
        if fp and (fp.get("大小") != st.st_size or fp.get("修改时间") != int(st.st_mtime)):
            print("[AI 识别] 素材已更新，重新识别")
            raise ValueError('新生成的识别结果与素材指纹不一致')
        if data.get("识别方案版本") != 2 or data.get("视觉状态") not in {"成功", "部分失败", "未请求", "失败"}:
            # The unified semantic archive is a separate schema. Reattach the
            # validated frame-analysis metadata before rejecting it as stale.
            if not explicit and legacy_path.exists():
                legacy = json.loads(legacy_path.read_text(encoding="utf-8-sig"))
                if legacy.get("识别方案版本") == 2 and legacy.get("视觉状态") in {"成功", "部分失败", "未请求", "失败"}:
                    for key in ("识别方案版本", "视觉状态", "视觉模型", "跨模态对齐模型", "素材指纹"):
                        if key in legacy:
                            data[key] = legacy[key]
                    if not data.get("视觉理解"):
                        data["视觉理解"] = legacy.get("视觉理解") or []
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                else:
                    raise ValueError("新生成的识别结果结构无效")
            else:
                raise ValueError("新生成的识别结果结构无效")
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    # 兼容没有模型文本层的旧统一档案：确定性档案不能阻断原有逐句剪辑。
    if not explicit and path.name.startswith("video-understanding-") \
            and not data.get("文本理解") and legacy_path.exists():
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        data["文本理解"] = legacy.get("文本理解") or {}
        data["视觉理解"] = legacy.get("视觉理解") or data.get("视觉理解") or []
        data["文本理解模型"] = legacy.get("文本理解模型", "")
    if not explicit and legacy_path.exists():
        # 统一档案保留下游信号结构；方案版本和视觉完成状态以原始识别档案为准。
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        for key in ("识别方案版本", "视觉状态", "视觉模型"):
            if key in legacy:
                data[key] = legacy[key]
        if not data.get("视觉理解"):
            data["视觉理解"] = legacy.get("视觉理解", [])
    if data.get("识别方案版本") != 2 or data.get("视觉状态") not in {"成功", "部分失败", "未请求", "失败"}:
        raise ValueError("识别结果版本过期或结构无效；停止使用空结果剪辑")
    return data


def wrap_text(text: str, max_chars: int) -> str:
    """字幕断行：优先在标点处断，其次到字数上限就断，每行尽量等长。"""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    punct = "，。！？；、,.!?;：:"
    units = "厘米周个月年天次个条斤克升米%分秒"
    lines, cur, i = [], "", 0
    while i < len(text):
        cur += text[i]
        if len(cur) >= max_chars:
            nxt = i + 1
            look = nxt
            while look < len(text) and look < nxt + 3 and text[look] not in punct:
                look += 1
            # 数字与单位不拆开（如 1.6 / 厘米）
            if text[i].isdigit() and look - 1 < len(text) and text[min(i + 1, len(text) - 1)] in "厘米周个月年天次斤克升分秒":
                i += 1
                cur += text[i]
            if look < len(text) and text[look] in punct:
                cur += text[nxt:look + 1]
                i = look
            lines.append(cur.strip())
            cur = ""
        i += 1
    if cur.strip():
        lines.append(cur.strip())
    return "\n".join(lines)



def match_broll(wrapped: list[dict], shots: list[dict], key: str) -> list[dict]:
    """让模型决定哪几句覆盖 B-roll：钩子与落点保持人物出镜，只覆盖相关的展开与证据。"""
    if not shots:
        return []
    lines = [f'{i+1}. [{w.get("角色","?")}] {w["text"]}' for i, w in enumerate(wrapped)]
    catalog = [f'{s["id"]} | {s.get("类型","")} | {s.get("描述","")[:60]}' for s in shots]
    prompt = ("你是短视频剪辑师。主片是口播，按顺序列在下面（含角色）。另有 B-roll 素材清单。\n"
              "决定哪几句用 B-roll 覆盖画面（覆盖时人声不变），哪几句保持人物出镜。\n"
              "规则：钩子与落点必须保持人物出镜；只有 B-roll 描述与该句内容相关时才覆盖；不相关就不要选。\n"
              "只输出 JSON：{\"匹配\": [{\"句号\": 1, \"broll\": \"B03\", \"原因\": \"十个字内\"}]}\n\n"
              "口播：\n" + "\n".join(lines) + "\n\nB-roll 清单：\n" + "\n".join(catalog))
    data = None
    try:
        if key:
            r = understand.post_json("https://api.deepseek.com/v1/chat/completions",
                                     {"model": "deepseek-v4-pro", "max_tokens": 2000,
                                      "response_format": {"type": "json_object"},
                                      "messages": [{"role": "user", "content": prompt}]},
                                     {"Authorization": "Bearer " + key}, timeout=300)
            msg = r["choices"][0]["message"]
            data = understand.extract_json(msg.get("content") or msg.get("reasoning_content") or "")
    except Exception as exc:
        print(f"[B-roll] 模型匹配失败({type(exc).__name__})，改用关键词匹配")
    out = []
    if data and data.get("匹配"):
        byid = {s["id"]: s for s in shots}
        for m in data["匹配"]:
            idx = int(m.get("句号", 0)) - 1
            sid = m.get("broll")
            if 0 <= idx < len(wrapped) and sid in byid:
                out.append({"句号": idx + 1, "broll": sid, "原因": m.get("原因", ""), "镜头": byid[sid]})
    if not out:
        byid = {s["id"]: s for s in shots}
        for i, w in enumerate(wrapped):
            text = w["text"].replace("\n", "")
            best, score = None, 0
            for s in shots:
                hit = sum(1 for kw in ("手", "桌", "妆", "产", "宝", "肚", "房", "餐") if kw in text and kw in s.get("描述", ""))
                if hit > score:
                    best, score = s, hit
            if best and score >= 1:
                out.append({"句号": i + 1, "broll": best["id"], "原因": "关键词匹配", "镜头": byid[best["id"]]})
    return out


def synthesize_voiceover(text: str, voice: str, name: str) -> tuple[Path, Path]:
    """调用 tts_voice 的 Edge-TTS 逻辑生成旁白 mp3 + 词级 SRT（免费、无 Key）。

    输出固定落到 04-assets/voice/<name>-voiceover.{mp3,srt}。
    tts_voice._speak 是 async 函数，这里用 asyncio.run 同步驱动。
    """
    import asyncio

    import tts_voice  # scripts 目录已在 sys.path 上
    mp3 = ROOT / "04-assets" / "voice" / f"{name}-voiceover.mp3"
    srt = ROOT / "04-assets" / "voice" / f"{name}-voiceover.srt"
    mp3.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(tts_voice._speak(text.strip(), voice, mp3, srt))
    if not mp3.exists() or mp3.stat().st_size == 0:
        raise RuntimeError(f"配音音频未生成或为空: {mp3}")
    return mp3, srt


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 代剪辑")
    ap.add_argument("--video", help="素材文件（原片或已标准化视频）")
    ap.add_argument("--style", help="风格 id，见 content-library/styles.json（--auto-style 时可不传）")
    ap.add_argument("--list-styles", action="store_true", help="列出全部可用风格后退出，不执行剪辑")
    ap.add_argument("--auto-style", action="store_true",
                    help="自动决策风格：按语义档案/降级特征推荐 Top3 并采用 Top1；与 --style 同传时以本开关为准")
    ap.add_argument("--transcript", help="逐字稿 JSON（默认按素材名在 02-transcript 里找）")
    ap.add_argument("--framewise-status", help="完整逐帧视觉扫描 status.json；只接受完整扫描用于决策")
    ap.add_argument("--name", help="成片名（默认 素材名-风格id）")
    ap.add_argument("--cover", help="封面文案（默认取第一句前 14 字，建议自己写）")
    ap.add_argument("--cta", help="行动指令（默认按风格目标给一个通用值）")
    ap.add_argument("--bgm", help="背景音乐文件（默认取 04-assets/bgm 下第一个音频）")
    ap.add_argument("--publish", action="store_true", help="生成后复制到剪映草稿目录")
    ap.add_argument("--no-build", action="store_true", help="只产出决策单，不生成草稿")
    ap.add_argument("--smart", action="store_true", help="用 AI 识别结果辅助剪辑（语义切点、删除建议、关键词、封面候选）")
    ap.add_argument("--understand", help="已有的识别结果 JSON（默认按素材名找 work/understand-*.json）")
    ap.add_argument("--semantic", action="store_true",
                    help="启用视频级语义档案辅助决策（自动探测 work/semantic-<素材名>.json；用画面高光候选做钩子交叉投票）")
    ap.add_argument("--semantic-path",
                    help="手动指定语义档案 JSON 路径（默认 work/semantic-<素材名>.json）；配合 --semantic 使用")
    ap.add_argument("--keep-pauses", action="store_true", help="保留原有停顿，不做去空档处理")
    ap.add_argument("--pause-threshold", type=float, default=0.6, help="超过该秒数的停顿才剪掉（默认 0.6）")
    ap.add_argument("--broll-index", help="B-roll 画面索引 JSON（broll_index.py 生成）")
    ap.add_argument("--beats", help="节拍 JSON（beats.py 生成）：画面按节拍切")
    ap.add_argument("--drop-blurry", action="store_true", help="自动丢弃画质不佳（模糊）的句子")
    ap.add_argument("--force-hook-first", action="store_true",
                    help="即使钩子已在前 3 秒也强行前置（默认只在钩子被埋到 3 秒后才搬）")
    ap.add_argument("--huazi", help="花字素材 id（见 config/effects_catalog.json，需本机已有缓存）")
    ap.add_argument("--huazi-where", default="cover", choices=["cover", "all"],
                    help="花字挂载位置：cover=只挂封面文字（默认），all=所有文字")
    ap.add_argument("--no-kinetic-text", action="store_true",
                    help="关闭 ASR 重点花字候选生成（智能剪辑默认开启；失败时保留普通字幕/关键词高亮）")
    # —— 旁白配音（Edge-TTS，免费无 Key）——
    ap.add_argument("--voiceover", help="旁白文本（提供则启用配音）")
    ap.add_argument("--voiceover-file", help="旁白文本文件（UTF-8），与 --voiceover 二选一")
    ap.add_argument("--voice", default=None,
                    help="Edge-TTS 音色（默认读风格的 剪辑参数.配音.voice，回退 zh-CN-XiaoxiaoNeural）")
    ap.add_argument("--voiceover-volume", type=float, default=1.0, help="配音音量，默认 1.0")
    ap.add_argument("--mute-original", dest="mute_original", action="store_true", default=True,
                    help="启用配音时把原片视频音量降为 0（默认开）")
    ap.add_argument("--no-mute-original", dest="mute_original", action="store_false",
                    help="保留原片人声，不静音")
    ap.add_argument("--keyframes", default=None,
                    choices=["none", "slow-zoom", "fade-in", "slow-zoom+fade-in"],
                    help="镜头级关键帧动画：slow-zoom 缓慢放大 / fade-in 开头淡入 / 两者叠加（默认读风格 剪辑参数.关键帧，回退 none）")
    ap.add_argument("--no-publish", action="store_true",
                    help="不发布到剪映草稿目录（默认即不发布，仅为兼容命令写法）")
    # -- v3.7.0 视觉智能适配引擎 --
    ap.add_argument("--visual-adapter", dest="visual_adapter", action="store_true", default=True,
                    help="开启视觉智能适配：按台词语义自动叠加数字卡/警示标签/动作图标等（默认开）")
    ap.add_argument("--no-visual-adapter", dest="visual_adapter", action="store_false",
                    help="关闭视觉智能适配")
    ap.add_argument("--visual-density", dest="visual_density", default="medium",
                    choices=["low", "medium", "high"],
                    help="视觉叠加密度：low=只叠强语义，medium=默认，high=普通句也加关键词高亮")
    # -- v3.7.1 模板化剪辑：套用已存剪辑模板（ID 或 JSON 路径），模板参数覆盖风格卡
    ap.add_argument("--template", dest="template", default=None,
                    help="剪辑模板 ID（content-library/templates/<id>.json）或模板 JSON 路径；"
                         "模板优先于 --style 的镜头时长/字幕行宽/关键帧")
    # -- v5.2.0 Copilot 方法论：成片后用 Agent(Criticism 迭代+偏好学习+断点队列) 复审
    ap.add_argument("--copilot", action="store_true",
                    help="成片后启用 Copilot 方法论复审（Codex 蒸馏：Critic 迭代 + 偏好学习 + 计划队列）")
    # -- v5.3.0 理解驱动剪辑：内容地图 + 视听意图（强调关键帧 / B-roll 配图 / BGM 分段）
    ap.add_argument("--intent", action="store_true",
                    help="v5.3 智能理解：生成内容地图+视听意图，应用于强调关键帧/B-roll 配图/BGM 分段")
    ap.add_argument("--intent-path",
                    help="手动指定意图 JSON 路径（默认 work/intent-<素材名>.json）；配合 --intent 使用")
    ap.add_argument('--no-knowledge', action='store_true',
                    help='关闭全学习库驱动的创作意图；保留原有基础剪辑流程')
    ap.add_argument('--goal', default='', help='本片制作目标、受众与必须保留的内容')
    ap.add_argument('--fast-analysis', action='store_true',
                    help='不执行全片逐帧画质扫描；转写、剪辑决策与现有学习库检索不变')
    args = ap.parse_args()
    if not args.no_knowledge:
        args.intent = True
        if not args.style and not args.template:
            args.auto_style = True

    # --list-styles：只列风格后退出，不执行剪辑
    if args.list_styles:
        for s in list_styles():
            print(f'{s["id"]:<22} {s["名称"]:<14} 目标=[{",".join(s["目标"])}]  适用=[{",".join(s["适用"])}]')
        return 0

    if not args.video:
        print("需要 --video <素材>（或用 --list-styles 只列风格）", file=sys.stderr)
        return 2

    video = Path(args.video)
    if not video.is_absolute():
        video = ROOT / video
    if not video.exists():
        print(f"找不到素材: {video}", file=sys.stderr)
        return 1

    # v4.0.0 稳定性加固：步骤级降级 + 批处理断点续跑。
    # 批处理进度仅在外部批量循环设置环境变量 AICUT_BATCH_JOB 时启用；
    # 单视频直接运行（默认）行为与返回值完全不变。
    from aicut.resilience import BatchProgress, execute_step  # noqa: E402
    _batch_job = os.environ.get("AICUT_BATCH_JOB", "").strip()
    _bp = BatchProgress(_batch_job) if _batch_job else None
    if _bp is not None:
        if _bp.is_completed(str(video)):
            print(f"[批处理] 该素材已完成，跳过：{video}")
            return 0
        _bp.mark_started(str(video))

    # 选风格：--auto-style 自动决策，否则 --style 必填
    if args.auto_style:
        from aicut import stylematch  # noqa: E402
        if args.style:
            print(f"[自动选风格] 同时指定了 --style {args.style}，以 --auto-style 推荐为准")
        sem_doc, sp_sum, tr_text = _auto_style_inputs(video)
        res = stylematch.match_style(semantic_doc=sem_doc, shotprof_summary=sp_sum,
                                     transcript_text=(args.goal + ' ' + tr_text).strip())
        recs = res.get("推荐") or []
        print(f"[自动选风格] 匹配方式={res.get('匹配方式')}  使用输入={res.get('使用的输入')}")
        for i, r in enumerate(recs, 1):
            print(f"  Top{i} {r['id']:<20} {r['名称']:<12} 置信度={r['置信度']}  理由：{r['理由']}")
        if not recs:
            print("[自动选风格] 未得到任何推荐，回退 koubo-zhishi", file=sys.stderr)
            sid = "koubo-zhishi"
        else:
            sid = recs[0]["id"]
            print(f"[自动选风格] 采用 Top1：{sid}")
        style = load_style(sid)
        # 自动决策且命中语义档案时，顺带开启 --semantic 的高光交叉投票
        if sem_doc is not None and not args.semantic:
            args.semantic = True
    elif args.style:
        style = load_style(args.style)
    else:
        # v3.7.1：用户只给 --template 没给 --style 时，用模板 style_hint 兜底风格
        _tpl_fallback = _load_template(args.template)
        if _tpl_fallback is not None:
            hint = _tpl_fallback.get("style_hint") or "koubo-zhishi"
            style = load_style(hint)
            print(f"[模板] 未指定 --style，按模板 style_hint 加载风格卡 {hint}")
        else:
            print("需要 --style <风格id> 或 --auto-style（或 --template）", file=sys.stderr)
            return 2

    # -- v3.7.1 模板化剪辑：加载模板并用模板参数覆盖风格卡 --
    template_doc = _load_template(args.template)
    template_used_id = ""
    if template_doc is not None:
        params = style["剪辑参数"]
        tnotes = _merge_template_into_params(params, template_doc)
        template_used_id = template_doc.get("template_id", "")
        print(f"[模板] 已套用 {template_used_id}：" + "、".join(tnotes))
    else:
        params = style["剪辑参数"]

    # 风格驱动默认值：用户未显式指定 --voice / --keyframes 时用风格值覆盖
    if args.voice is None:
        args.voice = (params.get("配音") or {}).get("voice") or "zh-CN-XiaoxiaoNeural"
        print(f"[风格默认] 配音音色 <- {style['id']}.剪辑参数.配音.voice = {args.voice}")
    if args.keyframes is None:
        args.keyframes = params.get("关键帧") or "none"
        print(f"[风格默认] 关键帧 <- {style['id']}.剪辑参数.关键帧 = {args.keyframes}")

    name = args.name or f"{video.stem}-{style['id']}"
    from aicut.production import project_name
    project_name(name)
    # 旁白配音：Edge-TTS 免费生成 mp3 + 词级 SRT（--voiceover / --voiceover-file 二选一）
    voiceover_cfg = None
    if args.voiceover:
        vo_text = args.voiceover
    elif args.voiceover_file:
        vf = Path(args.voiceover_file)
        if not vf.is_absolute():
            vf = ROOT / vf
        vo_text = vf.read_text(encoding="utf-8")
    else:
        vo_text = ""
    if vo_text.strip():
        try:
            vo_mp3, vo_srt = synthesize_voiceover(vo_text, args.voice, name)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ 配音生成失败: {exc}", file=sys.stderr)
            return 1
        voiceover_cfg = {
            "file": vo_mp3.relative_to(ROOT).as_posix(),
            "srt": vo_srt.relative_to(ROOT).as_posix(),
            "voice": args.voice,
            "volume": float(args.voiceover_volume),
            "mute_original": bool(args.mute_original),
        }
        print(f"[配音] {vo_mp3.name}  音色 {args.voice}  音量 {args.voiceover_volume}"
              f"  静音原片={'是' if args.mute_original else '否'}")
    total = ffprobe_duration(video)
    if not args.fast_analysis:
        from aicut.media_integrity import audit_decode, resolve_original
        print('[素材完整性] 正在完整解码视频和音频，不以抽帧替代完整读取。', flush=True)
        decode_audit = audit_decode(video)
        original_source = resolve_original(video)
        if original_source != video.resolve():
            source_audit = audit_decode(original_source)
            decode_audit['original_source'] = source_audit
            decode_audit['complete'] = decode_audit['complete'] and source_audit['complete']
            print(f"[原片完整性] {original_source.name}：{source_audit['decoded_video_frames']} 帧；"
                  '逐帧语义读取应使用此原片。', flush=True)
        decode_path = ROOT / 'work' / f'decode-integrity-{video.stem}.json'
        decode_path.write_text(json.dumps(decode_audit, ensure_ascii=False, indent=2), encoding='utf-8')
        if not decode_audit['complete']:
            print(f'[素材门禁] 完整解码失败，停止决策。记录：{decode_path}', file=sys.stderr)
            return 1
        print(f"[素材完整性] 完整解码 {decode_audit['decoded_video_frames']} 帧；语义分析另行校验。", flush=True)
    short_s = float(params.get("镜头最短秒", 2.0))
    long_s = float(params.get("镜头最长秒", 4.0))
    max_chars = int(params.get("字幕每行最多字", 14))

    # v4.0.0 降级矩阵：transcribe 失败时降级为空逐句稿（use_srt_or_empty），不崩溃
    _ok_tr, segments, _err_tr = execute_step(
        "transcribe", load_segments, video, args.transcript, _fallback=[])
    if not _ok_tr:
        print(f"[降级·转写] {_err_tr}（按空逐句稿继续）", file=sys.stderr)
    understanding = None if args.fast_analysis else load_understanding(video, args.smart, args.understand,
                                                                        args.transcript)
    if args.fast_analysis and args.smart:
        print('[AI 识别] fast-analysis 已跳过全片画质/停顿扫描；继续使用 ASR 与学习库决策')
    visual_quality: dict | None = None
    visual_profiles: list[dict] = []
    if not args.fast_analysis:
        from aicut import shotprof  # noqa: E402
        try:
            print('[质量优先] 正在全片采样画质检查与逐镜头视觉理解；采样覆盖不代表逐帧语义理解。', flush=True)
            visual_quality = shotprof.build(video, use_cloud=False, quality_first=True)
            visual_profiles = visual_quality.get('shots') or []
            gate = visual_quality.get('visual_coverage') or shotprof.quality_coverage(visual_quality)
            audit_path = ROOT / 'work' / f'visual-quality-{video.stem}.json'
            audit_path.write_text(json.dumps(visual_quality, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"[质量优先] 帧样本 {gate.get('frame_samples')}/{gate.get('expected_frame_samples_min')}，"
                  f"逐镜头语义理解 {gate.get('independently_understood_shots')}/{gate.get('shot_count')}，"
                  f"状态={gate.get('status')}；记录={audit_path}")
            if not gate.get('complete'):
                print('[质量门禁] 感知证据覆盖不完整，停止自动剪辑决策：'
                      + '；'.join(gate.get('issues') or ['原因待查'])
                      + '。请检查审计记录中的失败镜头/抽帧时间后重试。', file=sys.stderr)
                return 1
        except Exception as exc:  # noqa: BLE001
            audit_path = ROOT / 'work' / f'visual-quality-{video.stem}.json'
            from aicut.paths import fingerprint  # noqa: E402
            failure = {
                'video': str(video), 'media_fingerprint': fingerprint(video),
                'status': 'incomplete', 'failure_class': type(exc).__name__,
                'frame_samples': 0, 'expected_frame_samples_min': max(1, int(total * 2) - 1),
                'affected_shots': [], 'error': str(exc)[:500],
                'next_step': '检查抽帧/本地视觉模型日志，再重试；未完成前不生成草稿'}
            audit_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"[质量门禁] 视觉扫描失败，已停止并写入 {audit_path}：{type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
        if understanding is None:
            understanding = {'视觉状态': '成功', '视觉理解': [], '质量优先档案': str(audit_path)}
        understanding['quality_first_visual'] = {
            'fingerprint': visual_quality.get('fingerprint'),
            'visual_coverage': gate,
            'summary': visual_quality.get('summary') or {},
        }
    text_ai = (understanding or {}).get("文本理解") or {}
    signal = (understanding or {}).get("信号识别") or {}
    keywords = [str(k) for k in (text_ai.get("关键词") or [])][:8]
    rhythm: dict[str, str] = {}
    for item in (text_ai.get("逐句") or []):
        if item.get("节奏") and item.get("句"):
            for _s in segments:
                if str(_s.get("text", "")).strip() == str(item["句"]).strip():
                    rhythm[str(round(float(_s["start"]), 2))] = str(item["节奏"])
                    break
    cover_hint = (text_ai.get("封面候选") or [""])[0]
    prefer_cuts = list(text_ai.get("语义切点_秒") or []) + list(signal.get("镜头切点_秒") or [])

    # —— 视频级语义档案（v3.5 理解强化）：--semantic 启用时读取，不启用则完全不参与决策 ——
    semantic_doc: dict | None = None
    if args.semantic:
        if args.semantic_path:
            semantic_paths = [Path(args.semantic_path)]
        else:
            semantic_paths = [
                ROOT / "work" / f"semantic-{video.stem}.json",
                ROOT / "work" / f"semantic-glm-{video.stem}.json",
            ]
        semantic_paths = [
            p if p.is_absolute() else ROOT / p for p in semantic_paths
        ]
        sp_path = next((p for p in semantic_paths if p.exists()), semantic_paths[0])
        if sp_path.exists():
            try:
                semantic_doc = json.loads(sp_path.read_text(encoding="utf-8"))
                if semantic_doc.get("主体") and not semantic_doc.get("主角"):
                    semantic_doc["主角"] = semantic_doc["主体"]
                if semantic_doc.get("主角") and semantic_doc.get("关键信息点"):
                    print(f"[语义档案] 已加载 {sp_path.name}：主角={str(semantic_doc.get('主角','?'))[:14]}  "
                          f"完整率={semantic_doc.get('字段完整率','?')}  "
                          f"高光候选={len(semantic_doc.get('高光候选') or [])}个")
                else:
                    print(f"[语义档案] {sp_path.name} 不完整，拒绝把不完整理解用于剪辑")
                    semantic_doc = None
            except Exception as exc:  # noqa: BLE001
                print(f"[语义档案] 读取失败({type(exc).__name__})，回退纯文本钩子")
                semantic_doc = None
        else:
            print(f"[语义档案] 未找到 {sp_path.name}，正在尝试本机深度理解…")
            try:
                from aicut import semantic_profile  # noqa: E402
                semantic_doc = semantic_profile.deep_build(
                    video, transcript_path=args.transcript, force=False)
                if not semantic_doc.get("主角") or not semantic_doc.get("关键信息点"):
                    raise RuntimeError("本机理解结果不完整")
                print(f"[语义档案] 已生成：来源={semantic_doc.get('来源','本机')}，"
                      f"高光候选={len(semantic_doc.get('高光候选') or [])}个")
            except Exception as exc:  # noqa: BLE001
                print(f"[语义档案] 本机理解失败({type(exc).__name__}: {exc})，停止本次智能剪辑",
                      file=sys.stderr)
                return 1

    # -- v5.3.0 理解驱动剪辑：内容地图 + 视听意图（在语义档案/逐句分析之上再生成一层创作决策） --
    intent_doc: dict | None = None
    learning_applied = {'rhythm': {}, 'cuts': [], 'keywords': [], 'review': []}
    framewise_summary = None
    if args.framewise_status:
        from aicut.framewise import summarize_for_edit
        framewise_summary = summarize_for_edit(args.framewise_status)
        print(f"[逐帧证据] 完整扫描时间线已压缩为 {len(framewise_summary)} 条，输入剪辑意图模型")
    if args.intent:
        from aicut import edit_intent  # noqa: E402
        from aicut import llm_router
        from aicut import learning_context as learning
        query = ' '.join(str(s.get('text', '')) for s in segments)
        query += ' ' + style.get('名称', '') + ' ' + str(style.get('目标', ''))
        query = args.goal + ' ' + query
        # Add only the selected style's topic, not a fixed bag containing every
        # visual craft term. Generic terms such as "电影镜头/花字/转场" widened
        # each query and surfaced visually unrelated studies as if on-topic.
        style_features = style.get('适用素材特征') or {}
        style_terms = [style.get('描述', '')]
        for key in ('内容类型', '主角类型', '场景类型', '情绪基调', '适合结构'):
            value = style_features.get(key, [])
            style_terms.extend(value if isinstance(value, list) else [str(value)])
        query += ' 本片题材与风格：' + ' '.join(str(x) for x in style_terms if x)
        if framewise_summary:
            query += ' 全片可见画面：' + '；'.join(
                f"{r['subject']} {r['action']} {' '.join(r['objects'])} {r['visible_text']}"
                for r in framewise_summary)[:1400]
        # Make creative evidence retrieval scene-aware. Transcript-only retrieval
        # can surface visually unrelated examples (for example, pet footage for
        # a seated talking-head); use persisted visual observations as query terms
        # while retaining their unreviewed provenance.
        visual_terms = visual_query_terms(visual_profiles or [])
        if visual_terms:
            query += ' 自动画面观察（未人工复核，仅用于检索相似画面）：' + ' '.join(
                dict.fromkeys(visual_terms))[:900]
        # Visual profiles plus the selected style above request scene-matched
        # examples without injecting unrelated craft categories.
        bundle = learning.retrieve(query) if not args.no_knowledge else {'evidence': [], 'signature': 'disabled'}
        key = learning.cache_key(segments, text_ai, semantic_doc, bundle,
                                 {'video': str(video.resolve()), 'style': style, 'template': template_used_id,
                                  'goal': args.goal, 'intent_schema': edit_intent.INTENT_SCHEMA_VERSION,
                                  'decision_routes': [(b.name, b.model, b.base_url)
                                                      for b in llm_router.route_backends('decision')],
                                  'visual_fingerprint': (visual_quality or {}).get('fingerprint'),
                                  'visual_coverage': (visual_quality or {}).get('visual_coverage'),
                                  'visual_evidence': visual_profiles,
                                  'framewise_summary': framewise_summary})
        print(f"[学习库] 索引 {bundle.get('indexed_chunks', 0)} 条知识片段，召回 {len(bundle['evidence'])} 条；"
              f"覆盖 {len(bundle.get('coverage', {}))} 类来源")
        for warning in bundle.get('warnings', []):
            print(f'[学习库警告] {warning}')
        ipath = Path(args.intent_path) if args.intent_path else ROOT / 'work' / 'intents' / f'{key}.json'
        if not ipath.is_absolute():
            ipath = ROOT / ipath
        intent_doc = edit_intent.load_intent(ipath) if ipath.exists() else None
        if intent_doc and not intent_cache_matches(intent_doc, key):
            intent_doc = None
        if intent_doc is None:
            print("[意图] 首次运行，正在生成内容地图与视听意图…")
            try:
                intent_doc = edit_intent.generate_intent(segments, text_ai, semantic_doc,
                                                         meta={"video": str(video), 'goal': args.goal},
                                                         learning_context=bundle,
                                                         visual_profiles=visual_profiles,
                                                         framewise_summary=framewise_summary)
                intent_doc['_learning_cache_key'] = key
                ipath.parent.mkdir(parents=True, exist_ok=True)
                edit_intent.save_intent(intent_doc, ipath)
            except Exception as exc:  # noqa: BLE001
                print(f"[意图] 生成失败({type(exc).__name__}: {exc})，本片不启用意图层")
                intent_doc = None
        if not intent_doc or (intent_doc.get('_meta') or {}).get('degraded'):
            print('[决策门禁] 模型未完成有效决策，停止自动制作；保留分析记录供重试。', file=sys.stderr)
            return 1
        from aicut.intent_review import review_intent
        source_review = review_intent(intent_doc, segments)
        if source_review['blocking']:
            review_path = ROOT / 'work' / f'{name}.intent-review.json'
            review_path.write_text(json.dumps(source_review, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'[决策门禁] 方案与原始素材存在冲突，停止制作：{review_path}', file=sys.stderr)
            return 4
        if intent_doc:
            learning_applied = learning.decisions(intent_doc, segments, smart=args.smart)
            rhythm.update(learning_applied['rhythm'])
            keywords = list(dict.fromkeys(keywords + learning_applied['keywords']))[:16]
            print("[意图] ===== AI 创作决策单 =====")
            print(edit_intent.intent_decision_sheet(intent_doc))
            print("[意图] ===== 决策单结束 =====")


    cfg = cleanup.load_rules()
    from aicut.material_screening import screen_transcript
    screening=screen_transcript(segments,total) if segments else []
    for frame in ((understanding or {}).get("视觉理解") or []):
        if frame.get("有效") and frame.get("与语音关系") == "不匹配":
            t = float(frame.get("t", 0))
            screening.append({"start": round(max(0, t - .8), 3), "end": round(min(total, t + .8), 3),
                              "kind": "asr_visual_mismatch", "action": "review",
                              "reason": "抽样画面与同时间ASR不一致；可能是B-roll/转场，先复核再裁剪",
                              "text": frame.get("附近语音", ""), "frame_id": frame.get("id"),
                              "provenance": "automatic_alignment_unreviewed"})
    (ROOT/'work'/f'{name}.material-screening.json').write_text(json.dumps(screening,ensure_ascii=False,indent=2),encoding='utf-8')
    cut_ranges = cleanup.find_cut_ranges(segments, cfg, total) if segments else []
    cut_ranges.extend([[r['start'],r['end']] for r in screening if r['action']=='auto_remove' and r['kind']=='speech_repeat'])
    cut_ranges.extend(learning_applied['cuts'])
    ai_deleted: list[str] = []
    if args.smart and understanding:
        dels = [str(d.get("文本", "")) for d in (text_ai.get("删除建议") or []) if d.get("文本")]
        for seg in segments:
            if any(d and d in str(seg.get("text", "")) for d in dels):
                # Ungrounded model suggestions are review candidates, never deletion authority.
                screening.append({'start':float(seg['start']),'end':float(seg['end']),
                                  'kind':'model_delete_suggestion','action':'review','text':seg.get('text',''),
                                  'reason':'needs independent source evidence'})
    if args.smart and understanding and not args.keep_pauses:
        pauses = [p for p in (signal.get("停顿区间_秒") or []) if float(p[1]) - float(p[0]) >= args.pause_threshold]
        for p in pauses:
            a,b=float(p[0]),float(p[1])
            # VAD gaps can contain breathing, demonstrations, or ASR omissions.
            screening.append({'start':a,'end':b,'kind':'pause','action':'review',
                              'reason':'silence is not proof of irrelevant visual content'})
        if pauses:
            print(f"[停顿复核] 标记 {len(pauses)} 处空档，未无条件删除")
    (ROOT/'work'/f'{name}.material-screening.json').write_text(json.dumps(screening,ensure_ascii=False,indent=2),encoding='utf-8')
    cut_ranges = cleanup.merge_ranges(cut_ranges) if cut_ranges else []
    keeps = cleanup.keep_ranges(cut_ranges, total, cfg) if cut_ranges else [[0.0, total]]
    if cut_ranges:
        rhythm = {str(round(cleanup.map_time(float(start), cut_ranges), 2)): pace
                  for start, pace in rhythm.items()}
        kept = cleanup.drop_hidden(segments, cut_ranges)
        for seg in kept:
            seg["start"] = cleanup.map_time(float(seg["start"]), cut_ranges)
            seg["end"] = cleanup.map_time(float(seg["end"]), cut_ranges)
        total_clean = sum(e - s for s, e in keeps)
        print(f"[粗剪] 删除 {len(cut_ranges)} 段，成片 {total_clean:.1f}s（原 {total:.1f}s）")
        if ai_deleted:
            print(f"[识别粗剪] AI 判定删除：{'、'.join(ai_deleted)}")
        segments = kept

    clean_total = sum(e - s for s, e in keeps) if cut_ranges else total
    # 逐句取素材区间与速度（压缩 = 变速）
    actions = {}
    for it in (text_ai.get("逐句") or []):
        if it.get("句"):
            actions[str(it["句"]).strip()] = str(it.get("动作") or "保留")
    compress_speed = float(params.get("压缩倍速", 1.2))
    roles = {}
    for it in (text_ai.get("逐句") or []):
        if it.get("句"):
            roles[str(it["句"]).strip()] = str(it.get("角色") or "展开")

    items = []
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        src_s = cleanup.clean_to_source(float(seg["start"]), keeps,endpoint='start')
        src_e = cleanup.clean_to_source(float(seg["end"]), keeps,endpoint='end')
        dur = src_e - src_s
        if dur < 0.2:
            continue
        action = actions.get(text, "保留") if args.smart else "保留"
        speed = compress_speed if action == "压缩" else 1.0
        items.append({"text": text, "src": [src_s, src_e], "speed": speed, "target": dur / speed,
                      "sec": float(seg["start"]),
                      "rhythm": rhythm.get(str(round(float(seg["start"]), 2)), "中"),
                      "asr_low_confidence": bool(seg.get("低置信度")),
                      "asr_avg_logprob": seg.get("avg_logprob")})

    # 画质淘汰：丢掉落在模糊镜头里的句子
    if args.drop_blurry and understanding:
        blurry = (signal.get("可能画质不佳的片段") or [])
        if blurry and items:
            before = len(items)
            kept = []
            for it in items:
                hit = any(float(b["start"]) - 0.2 <= it["src"][0] and it["src"][1] <= float(b["end"]) + 0.2 for b in blurry)
                if hit:
                    print(f"[画质淘汰] 丢弃模糊片段中的句子：{it['text'][:16]}")
                    continue
                kept.append(it)
            items = kept

    # 节拍切分：让画面在节拍点上切换
    if args.beats:
        bp = Path(args.beats)
        if not bp.is_absolute():
            bp = ROOT / bp
        if bp.exists():
            beats = [float(x) for x in (json.loads(bp.read_text(encoding="utf-8")).get("节拍") or [])]
            if beats and items:
                new_items = []
                beat_set = set()
                for it in items:
                    s0, s1 = it["src"] if "src" in it else (it["src_start"], it["src_end"])
                    inside = [b for b in beats if float(s0) + 0.35 < b < float(s1) - 0.35]
                    if not inside:
                        new_items.append(it); continue
                    bounds = [float(s0)] + inside + [float(s1)]
                    for k in range(len(bounds) - 1):
                        piece = dict(it)
                        piece["src"] = [bounds[k], bounds[k + 1]]
                        piece["target"] = it["target"] * (bounds[k + 1] - bounds[k]) / max(1e-6, float(s1) - float(s0))
                        piece["beat_start"] = k > 0
                        new_items.append(piece)
                    beat_set.update(round(b, 2) for b in inside)
                items = new_items
                print(f"[节拍剪辑] 在 {len(beat_set)} 个节拍点切画面")

    # 钩子前置：只有钩子被埋在后面时才搬。短素材里硬搬会打断源片连续性，切出碎镜头。
    # v3.5：--semantic 启用时，用语义档案的画面高光候选做交叉投票。
    # 不启用 --semantic 时，hook 计算与搬移逻辑与旧版完全一致。
    hook_lead = 3.0
    hook_source = ""
    semantic_used = False
    ending_note = ""

    # 纯文本钩子（旧逻辑，无论是否 --semantic 都先算出来用于交叉投票）
    text_hook = str(text_ai.get("钩子句") or "").strip() if args.smart else ""

    # 语义高光候选时间点 -> 落在哪个 item 的源时间区间 -> 取该句文本做强候选
    sem_hook = ""
    if args.semantic and semantic_doc:
        semantic_used = True
        for h in (semantic_doc.get("高光候选") or []):
            try:
                ht = float(h.get("t", -1))
            except (TypeError, ValueError):
                continue
            hit_item = None
            for it in items:
                s0, s1 = it["src"]
                if s0 - 0.6 <= ht <= s1 + 0.6:
                    hit_item = it
                    break
            if hit_item is None and items:  # 兜底：取时间中点最近的 item
                hit_item = min(items, key=lambda it: abs((it["src"][0] + it["src"][1]) / 2 - ht))
            if hit_item and hit_item["text"].strip():
                sem_hook = hit_item["text"].strip()
                print(f"[语义档案] 高光 t={ht:.1f}s -> 句「{sem_hook[:24]}」（理由：{h.get('理由','')[:20]}）")
                break  # 只取第一个高光做钩子强候选

        # 结尾交叉确认：叙事时间轴最后一段 + shotprof ending_candidates
        tl = semantic_doc.get("叙事时间轴") or []
        if tl:
            last = tl[-1]
            ending_note = str(last.get("台词", "")).strip()
            print(f"[语义档案] 结尾交叉确认：时间轴末段「{ending_note[:24]}」")

    # 交叉投票决策最终 hook
    if args.semantic and semantic_doc and sem_hook:
        if text_hook and sem_hook == text_hook:
            hook = sem_hook
            hook_source = "both"
            print(f"[语义档案] 交叉投票一致：画面高光与 text_ai 钩子句重合，置信度高")
        elif text_hook:
            hook = sem_hook
            hook_source = "semantic"
            print(f"[语义档案] 交叉投票分歧：text_ai「{text_hook[:18]}」 vs 画面高光「{sem_hook[:18]}」，优先采用画面高光（结合了画面+文本）")
        else:
            hook = sem_hook
            hook_source = "semantic"
            print(f"[语义档案] 采用画面高光候选「{sem_hook[:18]}」做钩子")
    else:
        # 旧版行为（无 --semantic 或无可用高光）
        hook = text_hook
        hook_source = "text_ai" if hook else ""

    if hook:
        for idx, it in enumerate(items):
            if it["text"] == hook and idx > 0:
                if it["sec"] <= hook_lead and not args.force_hook_first:
                    print(f"[钩子前置] 跳过：钩子本来就在 {it['sec']:.1f}s"
                          f"（≤{hook_lead:.0f}s），搬动会打断源片连续性")
                else:
                    items.insert(0, items.pop(idx))
                    print(f"[钩子前置] 把「{hook[:18]}」移到开头（原位置 {it['sec']:.1f}s，来源={hook_source or 'text_ai'}）")
                break

    if not segments and total > 0:
        # A silent/B-roll source still has a picture timeline. No invented speech.
        at = 0.0
        while at < total - .05:
            end = min(total, at + max(1.0, long_s))
            items.append({'text': '', 'src': [at, end], 'speed': 1.0,
                          'target': end-at, 'sec': at, 'rhythm': '中'})
            at = end
        print(f'[纯画面] 保留 {total:.2f}s 画面，跳过台词和停顿裁剪')
    tr_name = params.get("转场调用名")
    tr_dur = float(params.get("转场时长秒", 0.5))
    clips, wrapped, timeline = [], [], 0.0
    run = None
    for idx, it in enumerate(items):
        if run is not None:
            contiguous = abs(it["src"][0] - run["end"]) < 0.06
            same_speed = abs(it["speed"] - run["speed"]) < 1e-6
            limit = rhythm_target(rhythm, it["sec"], short_s, long_s)
            if (not contiguous) or (not same_speed) or it.get("beat_start") \
                    or (run["target"] >= limit) or (run["target"] + it["target"] > long_s):
                run = None
        if run is None:
            run = {"file": str(video), "start": round(it["src"][0], 3), "end": round(it["src"][1], 3),
                   "speed": it["speed"], "target": it["target"],
                   "滤镜": params.get("滤镜"), "滤镜强度": params.get("滤镜强度", 40),
                   "i0": idx, "i1": idx}
            clips.append(run)
        else:
            run["end"] = round(it["src"][1], 3)
            run["target"] += it["target"]
            run["i1"] = idx
        if it['text'].strip():
            wrapped.append({"start": round(timeline, 2), "end": round(timeline + it["target"], 2),
                            "text": wrap_text(it["text"], max_chars),
                            "角色": roles.get(it["text"], "展开"),
                            "source_start": round(float(it["src"][0]), 3),
                            "source_end": round(float(it["src"][1]), 3)})
        timeline += it["target"]

    # 碎镜头修整：相邻两刀能接上时，把刀口在句子边界上前后挪，避免出现半秒镜头
    min_shot = short_s * 0.6
    for i in range(len(clips) - 1):
        a, b = clips[i], clips[i + 1]
        moved = 0
        while (b["target"] < min_shot and a["i1"] > a["i0"]
               and abs(b["start"] - a["end"]) < 0.06 and moved < 6):
            k = a["i1"]
            piece = items[k]
            if a["target"] - piece["target"] < min_shot:
                break
            a["end"] = round(piece["src"][0], 3)
            a["target"] -= piece["target"]
            b["start"] = round(piece["src"][0], 3)
            b["target"] += piece["target"]
            a["i1"] = k - 1
            b["i0"] = k
            moved += 1
        while (a["target"] < min_shot and b["i1"] > b["i0"]
               and abs(b["start"] - a["end"]) < 0.06 and moved < 6):
            k = b["i0"]
            piece = items[k]
            if b["target"] - piece["target"] < min_shot:
                break
            a["end"] = round(piece["src"][1], 3)
            a["target"] += piece["target"]
            b["start"] = round(piece["src"][1], 3)
            b["target"] -= piece["target"]
            a["i1"] = k
            b["i0"] = k + 1
            moved += 1
        if moved:
            print(f"[镜头平衡] 第 {i+1}/{i+2} 刀挪到句子边界，避免碎镜头")

    # v3.6 碎镜头保底合并：任何 target < floor 的镜头并入较短邻接镜头（源时间连续才合）
    # 字幕按句保留（被并入镜头的句子仍在 wrapped 里，文本不丢）
    floor_merge = min(1.8, max(1.0, short_s))
    while len(clips) > 1:
        merged_round = False
        for i in range(len(clips)):
            c = clips[i]
            if c["target"] >= floor_merge:
                continue
            left = clips[i - 1] if i - 1 >= 0 else None
            right = clips[i + 1] if i + 1 < len(clips) else None
            cand = []
            # 间隙 ≤1.5s 也允许合并（剪停顿后的小空隙，邻接镜头向外扩一格）
            if left and abs(c['start'] - left['end']) < .001 and abs(c['speed'] - left['speed']) < 1e-6:
                cand.append((left["target"], "L", left))
            if right and abs(right['start'] - c['end']) < .001 and abs(c['speed'] - right['speed']) < 1e-6:
                cand.append((right["target"], "R", right))
            if not cand:
                continue
            cand.sort(key=lambda x: x[0])  # 与较短邻接合并
            _, side, nb = cand[0]
            if side == "L":
                nb["end"] = c["end"]
                nb["target"] += c["target"]
                nb["i1"] = c["i1"]
                print(f"[碎镜头合并] 第{i+1}个镜头 {c['target']:.1f}s < {floor_merge:.1f}s，与第{i}个（左）合并")
            else:
                nb["start"] = c["start"]
                nb["target"] += c["target"]
                nb["i0"] = c["i0"]
                print(f"[碎镜头合并] 第{i+1}个镜头 {c['target']:.1f}s < {floor_merge:.1f}s，与第{i+2}个（右）合并")
            del clips[i]
            merged_round = True
            break
        if not merged_round:
            break

    # -- v5.3.0 意图强调：内容地图判定为核心结论/警告的句子，镜头额外推进放大强调 --
    # （必须在 i0/i1 移除前执行，用 items 索引对齐镜头）
    intent_kf_cnt = 0
    if intent_doc and wrapped:
        emph_texts = []
        for it in intent_doc.get("逐句意图") or []:
            if it.get("强调") == "keyframe" and it.get("句"):
                emph_texts.append(str(it["句"]).strip())
        emph_idx = set()
        for idx, _it in enumerate(items):
            t = str(_it.get("text", "")).strip()
            if any(e and (e in t or t in e) for e in emph_texts):
                emph_idx.add(idx)
        if emph_idx:
            for _c in clips:
                i0, i1 = int(_c.get("i0", 0)), int(_c.get("i1", 0))
                if any(i0 <= idx <= i1 for idx in emph_idx):
                    kf = _c.get("keyframes") or []
                    td = float(_c.get("target", 0.0))
                    kf = [x for x in kf if x.get("property") != "scale"]
                    kf.append({"property": "scale", "t": 0.0, "value": 1.0, "easing": "Smooth"})
                    kf.append({"property": "scale", "t": round(td, 3), "value": 1.14, "easing": "Smooth"})
                    _c["keyframes"] = kf
                    intent_kf_cnt += 1
        if intent_kf_cnt:
            print(f"[意图] 强调关键帧：{intent_kf_cnt} 个镜头（结论/警告句）缓慢推进至 1.14x")

    for _c in clips:
        _c.pop("i0", None)
        _c.pop("i1", None)
    # 配音开启且要求静音原片时，把每个镜头原音降为 0（build_draft 会读 clip.volume）
    if voiceover_cfg is not None and args.mute_original:
        for _c in clips:
            _c["volume"] = 0.0

    # 关键帧动画：缓慢推进(slow-zoom) / 开头淡入(fade-in)；默认 none 不写任何字段
    if args.keyframes != "none":
        kf_cnt = 0
        for _c in clips:
            kf: list[dict] = list(_c.get('keyframes') or [])
            if "slow-zoom" in args.keyframes and not any(k.get('property') == 'scale' for k in kf):
                td = float(_c.get("target", 0.0))
                if td > 0.1:
                    # 从 scale=1.0 平滑缓动到 1.08，结束点=片段目标时长
                    kf.append({"property": "scale", "t": 0.0, "value": 1.0, "easing": "Smooth"})
                    kf.append({"property": "scale", "t": round(td, 3), "value": 1.08, "easing": "Smooth"})
            if "fade-in" in args.keyframes:
                # 开头 0.3s 从 opacity=0 线性到 1.0
                kf.append({"property": "opacity", "t": 0.0, "value": 0.0, "easing": "Line"})
                kf.append({"property": "opacity", "t": 0.3, "value": 1.0, "easing": "Line"})
            if kf:
                _c["keyframes"] = kf
                kf_cnt += 1
        print(f"[关键帧] 模式={args.keyframes}，已为 {kf_cnt} 个镜头写入关键帧")

    for idx, clip in enumerate(clips):
        if idx < len(clips) - 1 and tr_name:
            clip["转场调用名"] = tr_name
            clip["转场时长秒"] = tr_dur
    if clips and params.get("开场特效"):
        clips[0]["特效"] = params["开场特效"]

    durations = [round(c["target"], 2) for c in clips]
    avg = sum(durations) / max(len(durations), 1)

    # 流程检查点自检：镜头时长是否落在风格区间、有没有碎镜头、钩子是否在前 3 秒
    warns: list[str] = []
    if durations:
        if avg < short_s or avg > long_s:
            warns.append(f"平均镜头 {avg:.1f}s 不在风格区间 {short_s}-{long_s}s")
        tiny = [d for d in durations if d < short_s * 0.6]
        if tiny and len(durations) > 1:
            warns.append(f"{len(tiny)} 个碎镜头 <{short_s * 0.6:.1f}s: {tiny}")
    hook_pos = None
    if hook:
        for _w in wrapped:
            if _w["text"].replace(chr(10), "") == hook.replace(chr(10), ""):
                hook_pos = _w["start"]
                break
        if hook_pos is not None and hook_pos > 3.0:
            warns.append(f"钩子出现在 {hook_pos:.1f}s（>3s）")
    print("[自检] " + ("通过：镜头时长与钩子位置符合风格" if not warns else "；".join(warns)))
    print(f"[切镜头] {len(clips)} 个镜头，平均 {avg:.1f}s（风格 {short_s}-{long_s}s，成片 {timeline:.1f}s）")
    print("[SHOTS_JSON] " + json.dumps({"count": len(clips), "durations": durations, "avg": round(avg, 2),
                                        "style": style["id"], "name": style["名称"]}, ensure_ascii=False))

    sub_path = ROOT / "02-transcript" / f"{name}.auto.json"
    from aicut.learning_execution import compile_subtitles
    wrapped, learning_execution = compile_subtitles(intent_doc or {}, wrapped, max_chars=12)
    if learning_execution['compiled_count']:
        print(f"[学习执行] 已将 {learning_execution['compiled_count']} 条字幕分句决策编译到输出逐字稿；待产物核验")
    sub_path.parent.mkdir(parents=True, exist_ok=True)
    sub_path.write_text(json.dumps({"name": name, "segments": wrapped}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[字幕] {len(wrapped)} 句，每行 ≤{max_chars} 字 -> {sub_path.name}")

    # 封面与落点
    first_text = segments[0]["text"].strip() if segments else ""
    cover_src = "首句"
    if args.cover:
        cover = args.cover
        cover_src = "用户指定"
    else:
        cover = _smart_cover(semantic_doc, cover_hint, first_text)
        cover_src = "语义关键信息点" if (semantic_doc and (semantic_doc.get("关键信息点") or [])) else "摘要/首句"
        ocover = _ollama_cover(semantic_doc, ' '.join(str(s.get('text', '')) for s in segments))
        if ocover:
            cover, cover_src = ocover, "Ollama 生成"
    cover_review = {'status': 'user_supplied'}
    if not args.cover:
        cover, cover_review = grounded_cover(cover, segments)
        if cover_review['status'] == 'replaced_unsupported_copy':
            print('[封面复核] 拒绝无原话依据的自动文案，改用台词摘录')
    print(f"[封面] {cover_src}: {cover}")
    cta = args.cta
    if not cta:
        spoken_cta = next((str(s.get('text', '')).strip() for s in reversed(segments)
                           if any(word in str(s.get('text', '')) for word in
                                  ('评论', '留言', '关注', '收藏', '转发', '私信', '点赞'))), '')
        cta = spoken_cta or STYLE_CTA.get(style["id"])
        cta_src = f"风格定制({style['id']})"
        if spoken_cta:
            cta_src = '原声行动指令'
        if not cta:
            for goal in style.get("目标", []):
                if goal in DEFAULT_CTA:
                    cta = DEFAULT_CTA[goal]
                    cta_src = f"目标通用({goal})"
                    break
        cta = cta or "关注看后续"
        print(f"[落点] 智能生成[{cta_src}]: {cta}")

    bgm_path = args.bgm
    if not bgm_path:
        bgm_dir = ROOT / "04-assets" / "bgm"
        if bgm_dir.exists():
            for f in sorted(bgm_dir.iterdir()):
                if f.suffix.lower() in AUDIO_EXT:
                    bgm_path = str(f)
                    break
    bgm = {"file": str(bgm_path), "volume": params.get("BGM音量", 0.15)} if bgm_path else None
    print(f"[BGM] {'无' if not bgm else Path(bgm['file']).name + ' 音量 ' + str(bgm['volume'])}")

    # B-roll 覆盖：识别画面内容后，把相关的句子配上对应画面
    broll_entries = []
    if args.broll_index:
        bi = Path(args.broll_index)
        if not bi.is_absolute():
            bi = ROOT / bi
        if bi.exists():
            shots = json.loads(bi.read_text(encoding="utf-8")).get("镜头") or []
            matches = match_broll(wrapped, shots, understand.load_key())
            for m in matches:
                w = wrapped[m["句号"] - 1]
                shot = m["镜头"]
                broll_entries.append({"file": shot["file"], "start": shot["start"], "end": shot["end"],
                                      "at": w["start"], "duration": round(w["end"] - w["start"], 2)})
                print(f'[B-roll] 第{m["句号"]}句「{w["text"][:14]}」覆盖 {shot["id"]}：{m.get("原因","")}')
            print(f"[B-roll] 共覆盖 {len(broll_entries)} 句")
            # -- v5.3.0 意图配图：AI 判定「没有画面讲不清」的句子，若未被覆盖则按语义关键词补配 --
            if intent_doc:
                from aicut.learning_context import align_sentences
                want = []
                for index, it in align_sentences(intent_doc, wrapped):
                    if it.get("配图") and it.get("配图描述"):
                        want.append((index, str(it["配图描述"])))
                covered = {m["句号"] - 1 for m in matches}
                for idx, desc in want:
                    if idx in covered or not (0 <= idx < len(wrapped)):
                        continue
                    wtxt = str(wrapped[idx]["text"]).replace("\n", "")
                    # 在镜头清单里按描述关键词/句文本关键词找最相关镜头
                    best, best_score = None, 0
                    for sh in shots:
                        blob = f"{sh.get('类型','')} {sh.get('描述','')} {sh.get('id','')} {str(sh.get('file',''))}"
                        score = sum(1 for ch in desc if ch in blob) + \
                            sum(1 for ch in wtxt[:20] if ch in blob and ch not in "的了是在有和与")
                        if score > best_score:
                            best, best_score = sh, score
                    if best and best_score >= 2:
                        w = wrapped[idx]
                        broll_entries.append({"file": best["file"], "start": best["start"], "end": best["end"],
                                              "at": w["start"], "duration": round(w["end"] - w["start"], 2)})
                        print(f'[意图配图] 第{idx + 1}句「{wtxt[:14]}」配 {best["id"]}（需求：{desc}）')
                if broll_entries:
                    print(f"[B-roll] 意图配图后共 {len(broll_entries)} 句覆盖画面")
    # -- v3.7.0 视觉智能适配：在最终时间线 wrapped 上跑语义->视觉规则 --
    # v4.0.0：visualmap 步骤按降级矩阵 skip（失败即按无叠加继续），不崩溃
    visual_plan = []
    visual_rows = (understanding or {}).get("视觉理解") or []
    visual_decision_audit = []
    visual_failed = (understanding or {}).get("视觉状态") not in {"成功", "部分失败"} \
        or not any(item.get("有效") for item in visual_rows)
    visual_allowed = args.visual_adapter and wrapped and not (args.smart and visual_failed)
    if args.smart and visual_failed:
        print("[视觉适配] 已跳过：视觉理解失败，禁止用装饰性图标/花字伪造画面理解",
              file=sys.stderr)
    if visual_allowed:
        from aicut import visualmap  # noqa: E402
        _vp_segs = [{"start": float(w["start"]), "end": float(w["end"]),
                     "source_start": float(w.get("source_start", w["start"])),
                     "source_end": float(w.get("source_end", w["end"])),
                     "text": str(w["text"]).replace(chr(10), "")} for w in wrapped]
        _ok_vp, _vp, _err_vp = execute_step(
            "visualmap", visualmap.plan, _vp_segs, semantic_doc, args.visual_density, _fallback=[])
        if _vp:
            visual_plan = _vp
            _vs = getattr(_vp, "_stats", {})
            # Do not place stickers/flower text over a source moment whose
            # independently computed ASR/visual alignment says it conflicts.
            # This only affects decorative overlays; the spoken subtitle stays.
            aligned = [x for x in visual_rows if x.get("有效") and x.get("与语音关系")]
            kept_visual = []
            for overlay in visual_plan:
                line = next((s for s in _vp_segs
                             if s["start"] < float(overlay.get("t1", 0))
                             and float(overlay.get("t0", 0)) < s["end"]), None)
                source_t = ((line["source_start"] + line["source_end"]) / 2) if line else None
                nearest = min(aligned, key=lambda x: abs(float(x.get("t", 0)) - source_t)) if aligned and source_t is not None else None
                if nearest and nearest.get("与语音关系") == "不匹配":
                    visual_decision_audit.append({"action": "suppressed_overlay_review",
                        "start": overlay.get("t0"), "end": overlay.get("t1"),
                        "source_text": overlay.get("source_text", ""),
                        "reason": nearest.get("对齐依据", "最近抽样帧与语音不匹配"),
                        "visual_frame": nearest.get("id"), "provenance": "automatic_alignment_unreviewed"})
                    continue
                if nearest and nearest.get("与语音关系") == "无法判断":
                    visual_decision_audit.append({"action": "held_overlay_for_review",
                        "start": overlay.get("t0"), "end": overlay.get("t1"),
                        "source_text": overlay.get("source_text", ""),
                        "reason": "缺少足够的视听对应证据", "visual_frame": nearest.get("id"),
                        "provenance": "automatic_alignment_unreviewed"})
                    continue
                kept_visual.append(overlay)
            visual_plan = kept_visual
            (ROOT / "work" / f"{name}.visual-alignment-review.json").write_text(
                json.dumps(visual_decision_audit, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[视觉适配] 密度={args.visual_density} 计划 {len(visual_plan)} 项（命中 {_vs.get('命中句数',0)}/{_vs.get('总句数',0)} 句）")
            for _it in visual_plan[:5]:
                print(f"    {_it['type']:<16} {_it['content']:<10} {_it['position']:<10} {_it['reason']}")
        else:
            print(f"[视觉适配] {_err_vp}，按无叠加继续", file=sys.stderr)
            visual_plan = []
    visual_adapter_info = {"enabled": bool(args.visual_adapter),
                           "density": args.visual_density,
                           "plan_count": len(visual_plan)}

    kinetic_cards = []
    kinetic_style = "clean"
    kinetic_decision = {"status": "not_requested"}
    if args.smart and not args.no_kinetic_text and items:
        try:
            from aicut.kinetic_text_agent import generate_cards  # noqa: E402
            media_id = str(video.resolve())
            card_segments = []
            card_clips = []
            for idx, item in enumerate(items, 1):
                src_start, src_end = map(float, item["src"])
                card_segments.append({"start": src_start, "end": src_end,
                                      "text": str(item.get("text", "")),
                                      "低置信度": bool(item.get("asr_low_confidence")),
                                      "avg_logprob": item.get("asr_avg_logprob")})
                card_clips.append({"media_id": media_id, "start": src_start, "end": src_end,
                                   "speed": float(item.get("speed", 1)),
                                   "first_segment": idx, "last_segment": idx})
            generated = generate_cards(card_clips, [{"id": media_id, "segments": card_segments}],
                                       goal=args.goal or style.get("名称", style.get("id", "")))
            kinetic_style = generated.get("style", "clean")
            # The frame alignment is an independent guard. A conflict suppresses
            # decorative title cards and creates a review record; subtitles remain.
            for card in generated.get("cards", []):
                s0, s1 = float(card.get("source_start", 0)), float(card.get("source_end", 0))
                nearest = min((_x for _x in visual_rows if _x.get("有效")),
                              key=lambda _x: abs(float(_x.get("t", 0)) - (s0+s1)/2), default=None)
                if nearest and nearest.get("与语音关系") == "不匹配":
                    visual_decision_audit.append({"action": "suppressed_kinetic_text_review",
                        "start": card.get("start"), "end": card.get("end"), "source_quote": card.get("source_quote", ""),
                        "reason": nearest.get("对齐依据", "对应抽样帧与语音不匹配"),
                        "visual_frame": nearest.get("id"), "provenance": "automatic_alignment_unreviewed"})
                    continue
                kinetic_cards.append(card)
            kinetic_decision = {"status": "complete", "decision": generated.get("decision"),
                                "reason": generated.get("decision_reason", ""),
                                "candidate_count": len(generated.get("cards", [])),
                                "retained_count": len(kinetic_cards),
                                "reviewed_examples_used": generated.get("reviewed_examples_used", 0)}
            print(f"[ASR花字] 模型决策={generated.get('decision')}，候选 {len(generated.get('cards', []))} 组，视听校验保留 {len(kinetic_cards)} 组")
        except Exception as exc:  # noqa: BLE001
            kinetic_decision = {"status": "degraded", "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}
            print(f"[ASR花字] 生成失败，保留字幕与ASR关键词高亮：{type(exc).__name__}: {str(exc)[:100]}")
    elif args.no_kinetic_text:
        kinetic_decision = {"status": "disabled_by_user"}
    (ROOT / "work" / f"{name}.visual-alignment-review.json").write_text(
        json.dumps(visual_decision_audit, ensure_ascii=False, indent=2), encoding="utf-8")

    _screen_auto = sum(1 for x in screening if x.get("kind") == "speech_repeat" and x.get("action") == "auto_remove")
    _screen_review = sum(1 for x in screening if x.get("action") == "review")
    _asr_low = sum(1 for x in screening if x.get("kind") == "asr_uncertain")
    _vm_rows = (understanding or {}).get("视觉理解") or []
    _vm_valid = sum(1 for x in _vm_rows if x.get("有效"))
    _vm_low_trust = sum(1 for x in _vm_rows if x.get("视觉可信级别", "").startswith("低"))
    _vm_mismatch = sum(1 for x in _vm_rows if x.get("与语音关系") == "不匹配")
    pipeline_manifest = {
        "schema_version": "material-recognition-pipeline-1",
        "project": name,
        "source": str(video.resolve()),
        "stages": [
            {"name": "full_frame_visual_observation",
             "status": "complete" if framewise_summary is not None else "not_run",
             "summary_events": len(framewise_summary or []),
             "status_file": args.framewise_status,
             "provenance": "automatic_visual_observation_unreviewed"},
            {"name": "source_sampling_and_quality", "status": "needs_review" if _vm_low_trust else "complete" if _vm_valid else "failed",
             "frames_requested": len(_vm_rows), "frames_valid": _vm_valid,
             "low_trust_frames": _vm_low_trust,
             "visual_model": (understanding or {}).get("视觉模型", "")},
            {"name": "asr_repeat_and_confidence_screen", "status": "complete" if segments else "missing_asr",
             "segments": len(segments), "exact_repeats_auto_remove": _screen_auto,
             "review_candidates": _screen_review, "low_confidence_asr": _asr_low,
             "policy": "only exact safe repeat candidates auto-remove; uncertain ASR and visual mismatch stay review-only"},
            {"name": "asr_visual_alignment", "status": (understanding or {}).get("视觉状态", "not_run"),
             "mismatch_candidates": _vm_mismatch,
             "candidate_review_file": f"work/{name}.material-screening.json"},
            {"name": "subtitles_and_asr_highlighted_overlays", "status": "complete" if wrapped else "no_timed_asr",
             "subtitle_segments": len(wrapped), "asr_highlight_terms": keywords,
             "overlay_items": len(visual_plan), "kinetic_text_cards": len(kinetic_cards),
             "kinetic_decision": kinetic_decision,
             "subtitle_file": str(sub_path.relative_to(ROOT))},
            {"name": "decoration_second_frame_review", "status": "pending_render",
             "note": "导出MP4后在花字/贴纸/特效时间点抽帧，自动评估干净度并保存联系表"},
            {"name": "randomized_final_frame_review", "status": "pending_render",
             "note": "导出MP4后再独立随机抽帧复审；联系表需人工确认美观程度"},
        ],
        "evidence": {"screening": f"work/{name}.material-screening.json",
                     "recognition": str((ROOT / "work" / f"understand-{video.stem}.json"))},
    }

    sheet = {
        "project": name,
        "timeline_prepared": True,
        "production_health": {"intent_requested": bool(args.intent),
                              "intent_available": bool(intent_doc),
                              "intent_degraded": bool((intent_doc or {}).get('_meta', {}).get('degraded')),
                              "understanding_requested": bool(args.smart),
                              "understanding_available": bool(understanding)},
        "template": "koubo-standard" if style["id"] != "kongjian-zhanshi" else "fangxing-showcase",
        "style": params.get("text_style_preset"),
        "width": 1080, "height": 1920, "fps": 30,
        "cover_text": cover, "cta": cta, "cover_copy_review": cover_review,
        "clips": clips,
        "subtitles_json": str(sub_path.relative_to(ROOT)),
        # Portable renderer is the normal path for new automatic edits. Face
        # retouch stays opt-in per project to avoid changing every portrait.
        "subtitle_layout": {"font_size_px": 58, "top_px": 1055,
                            "box_alpha": 0.70, "max_chars_per_line": 12,
                            # Keep the user-approved phrase-by-phrase display
                            # active in the normal production path.
                            "single_line_per_caption": True},
        "render_pipeline": {
            "subtitle_renderer": "drawtext", "title_renderer": "drawtext",
            "font_path": r"C:\Windows\Fonts\msyhbd.ttc",
            "portrait_beauty": {
                "enabled": False,
                "model": r"models\yunet\face_detection_yunet_2023mar.onnx",
                "strength": 0.65, "detect_every_frames": 8,
                "confidence": 0.5, "detect_width": 540, "crf": 19,
            },
        },
        "画面": {"滤镜": params.get("滤镜"), "滤镜强度": params.get("滤镜强度", 40)},
        "风格": style["id"], "结构": style.get("结构"), "钩子类型": style.get("钩子类型"),
        "template_used": template_used_id,
        "keywords": keywords,
        "broll": broll_entries,
        "semantic_used": bool(semantic_used),
        "hook_source": hook_source or ("text_ai" if hook else ""),
        "semantic": {"主角": (semantic_doc or {}).get("主角"), "适合结构": (semantic_doc or {}).get("适合结构"),
                     "高光候选数": len((semantic_doc or {}).get("高光候选") or []),
                     "字段完整率": (semantic_doc or {}).get("字段完整率"),
                     "结尾": ending_note} if semantic_used else None,
        "visual_plan": visual_plan,
        "kinetic_text": kinetic_cards,
        "kinetic_text_style": kinetic_style,
        "visual_adapter": visual_adapter_info,
        "recognition_pipeline": pipeline_manifest,
        "framewise_visual_evidence": framewise_summary,
        "自检": {"通过": not warns, "问题": warns,
                 "平均镜头秒": round(avg, 2), "钩子位置秒": hook_pos,
                 "风格区间秒": [short_s, long_s]},
    }
    if args.huazi:
        sheet["花字"] = {"素材": str(args.huazi), "位置": args.huazi_where}
    elif kinetic_cards:
        # Resolve a real installed Jianying text-effect asset at plan time so
        # the subsequent build_draft step attaches it to these ASR title cards.
        try:
            import effects as _effects  # noqa: E402
            live_effects = _effects.available_text_effects()
            if live_effects:
                chosen = live_effects[0]
                sheet["花字"] = {"素材": chosen["id"], "位置": "kinetic-title",
                                "自动选择": True, "素材名称": chosen["name"],
                                "可用缓存路径": chosen["path"]}
                kinetic_decision["native_text_effect"] = {
                    "status": "ready_to_apply", "resource_id": chosen["id"],
                    "name": chosen["name"], "target": "kinetic-title"}
                pipeline_manifest["stages"][3]["native_text_effect"] = "ready_to_apply"
            else:
                kinetic_decision["native_text_effect"] = {"status": "unavailable",
                    "reason": "本机花字素材缓存为空或已失效；保留内置动态字样式"}
                pipeline_manifest["stages"][3]["native_text_effect"] = "unavailable"
        except Exception as exc:  # noqa: BLE001
            kinetic_decision["native_text_effect"] = {"status": "degraded",
                "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}
            pipeline_manifest["stages"][3]["native_text_effect"] = "degraded"
    if bgm:
        sheet["bgm"] = bgm
        # -- v5.3.0 意图节奏：BGM 情绪分段附带到决策单（build_draft 逐步支持音量关键帧） --
        if intent_doc:
            segs = (intent_doc.get("节奏方案") or {}).get("bgm分段") or []
            if segs:
                sheet["bgm"]["segments"] = segs
                print(f"[意图] BGM 情绪分段 {len(segs)} 段："
                      + " | ".join(f"{s.get('开始秒')}-{s.get('结束秒')}s {s.get('情绪')}({s.get('强度')})" for s in segs[:4]))
    if voiceover_cfg is not None:
        sheet["voiceover"] = voiceover_cfg
    if intent_doc:
        sheet['learning_context'] = intent_doc.get('learning_context', {})
        sheet['learning_decisions'] = learning_applied['review']
        sheet['learning_application'] = intent_doc.get('learning_application', {'采用': [], '未采用': []})
        sheet['learning_execution'] = learning_execution
        sheet['visual_memory_reasoning'] = intent_doc.get('visual_memory_reasoning')
        sheet['visual_decision_audit'] = intent_doc.get('visual_decision_audit', {})
        sheet['learning_application_audit'] = {
            'retrieved_visual_studies': [
                {'id': d.get('id'), 'title': d.get('title'),
                 'application_status': d.get('application_status', 'retrieved_context_only'),
                 'evidence_status': d.get('evidence_status'),
                 'production_use_allowed': False}
                for d in (sheet['learning_context'].get('evidence') or [])
                if d.get('category') == 'paired_visual_study'],
            'knowledge_applied': False,
            'note': '模型声明只算待审候选；人工/成片验证前不记为已学会或已应用'
        }
        sheet['source_review'] = source_review
        if visual_quality:
            sheet['visual_quality'] = {
                'fingerprint': visual_quality.get('fingerprint'),
                'coverage': visual_quality.get('visual_coverage'),
                'profile_file': str(ROOT / 'work' / f'visual-quality-{video.stem}.json'),
            }
        sheet['architecture_decision'] = intent_doc.get('architecture_decision', {})
        sheet['intent_path'] = str(ipath)
        sheet['制作目标'] = args.goal

    sheet_path = ROOT / "03-scripts" / f"{name}.sheet.json"
    sheet['requires_plan_confirmation'] = True
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "work" / f"{name}.recognition-pipeline.json").write_text(
        json.dumps(pipeline_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[决策单] {sheet_path}")

    if args.no_build:
        return 0
    if sheet.get('requires_plan_confirmation'):
        import hashlib
        digest = hashlib.sha256(sheet_path.read_bytes()).hexdigest()
        print(f'[待确认方案] 决策单已保存。确认此版本后，使用 build_draft.py 的 '
              f'--approve-plan-sha256 {digest} 生成草稿。')
        return 0

    # v4.0.0 降级矩阵：draft_write = retry(2)/fatal。子进程崩溃时自动重试，
    # 仍失败则判为致命错误，终止并记录批处理失败。
    def _run_build():
        cmd = [str(ROOT / "venv" / "Scripts" / "python.exe"),
               str(ROOT / "scripts" / "build_draft.py"), str(sheet_path)]
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", cwd=str(ROOT), check=True)

    _ok_build, proc, _err_build = execute_step("draft_write", _run_build, _fallback=None)
    if not _ok_build:
        print(f"[严重·草稿写入失败] {_err_build}", file=sys.stderr)
        if _bp is not None:
            _bp.mark_failed(str(video), _err_build)
        return 1
    print(proc.stdout.strip())
    if proc.returncode != 0:
        print(proc.stderr.strip()[-600:], file=sys.stderr)
        if _bp is not None:
            _bp.mark_failed(str(video), f"build_draft 返回码 {proc.returncode}")
        return proc.returncode

    # v5.2.0 Copilot 方法论复审：模板成片 → Agent（Critic 迭代 + 偏好学习 + 断点队列）
    if args.copilot:
        from aicut.agent import Agent  # noqa: E402
        draft_dir = ROOT / "06-drafts" / name
        _goal = "  ".join(style.get("目标") or []) or "做一条成片"
        if draft_dir.exists():
            try:
                _ag = Agent(draft=draft_dir, goal=f"复审并完善：{_goal}",
                            copilot_method=True, use_rules=True,
                            max_iterations=2, task=f"{name}-copilot复审")
                _rr = _ag.run()
                _rev = _rr.get("review") or {}
                print(f"[Copilot复审] ok={_rr.get('ok')} 修复步骤={len(_rr.get('steps', []))} "
                      f"评审轮次={_rr.get('iteration_count')} "
                      f"score={_rev.get('overall_score')} ready_to_ship={_rev.get('ready_to_ship')}")
                if not _rr.get('ok') or not _rev.get('ready_to_ship'):
                    print('[待复审] 未通过 Copilot 复审，草稿保留，不发布')
                    return 4
            except Exception as _e:  # noqa: BLE001
                print(f"[Copilot复审] 失败（{_e}），不发布", file=sys.stderr)
                return 4
        else:
            print(f"[Copilot复审] 未找到成片草稿 {draft_dir}，跳过", file=sys.stderr)
            return 4
    from aicut.production import validate_draft
    validate_draft(ROOT / '06-drafts' / name)
    if args.publish and not args.no_publish:
        if args.intent and (not intent_doc or (intent_doc.get('_meta') or {}).get('degraded')):
            print('[待复审] 意图模型降级，已保留草稿，不自动发布')
            return 4
        import build_draft
        build_draft.publish(ROOT / '06-drafts' / name, build_draft.load_config())
    if _bp is not None:
        _bp.mark_completed(str(video), str(sheet_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
