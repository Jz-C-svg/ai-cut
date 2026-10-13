# -*- coding: utf-8 -*-
r"""内容架构引擎（v2.0）——三层级叙事结构决策 + 电影剪辑语言横切知识。

定位：
  风格（styles/*.json）解决「怎么剪」——转场/字幕/BGM/滤镜/节奏参数。
  架构（architectures/*.json）解决「讲什么结构」——钩子设计/段落功能/信息密度/CTA。
  电影语言（architectures/film-language.json）是横切知识——景别/机位/剪辑点/蒙太奇/转场叙事功能，
  不参与架构匹配，在方案生成时按内容层级注入相关规则。

三层级：
  shortform（<=3min）：13 类短视频架构
  longform（>3min）：纪录片/长vlog/知识课程/中视频/系列化/直播切片 等长视频架构
  film-level（>10min 或多镜头叙事）：电影级叙事结构（三幕/五幕/英雄之旅/节拍表）

决策链路：
  素材识别 → 时长层级判定（detect_content_layer）→ 架构匹配（按层级选匹配池）
  → 电影语言规则筛选（get_relevant_film_rules）→ 架构+风格+电影语言联合决策
  → edit_intent 注入 architecture + film_language 字段

用法：
    from aicut import architecture
    layer = architecture.detect_content_layer(segments, meta)
    arch = architecture.match_architecture(segments, semantic_doc, meta, content_layer=layer)
    film_lang = architecture.load_film_language()
    relevant = architecture.get_relevant_film_rules(film_lang, layer, arch["matched"]["id"])
    decision = architecture.combine_with_style(arch, style_card, film_language=relevant)
    print(architecture.architecture_decision_sheet(decision))
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .paths import CONTENT_LIBRARY

ARCH_DIR = CONTENT_LIBRARY / "architectures"
_INDEX_FILE = ARCH_DIR / "architectures.json"


# ───────────────────────── 加载与索引 ─────────────────────────

def _load_index() -> list[dict]:
    """从 architectures.json 索引加载所有架构元信息。"""
    if not _INDEX_FILE.exists():
        return _rebuild_index()
    try:
        data = json.loads(_INDEX_FILE.read_text(encoding="utf-8-sig"))
        items = data.get("architectures") if isinstance(data, dict) else data
        return items or []
    except Exception:
        return _rebuild_index()


def _rebuild_index() -> list[dict]:
    """扫描 architectures/*.json 重建索引。"""
    items: list[dict] = []
    if not ARCH_DIR.exists():
        return items
    for fp in sorted(ARCH_DIR.glob("*.json")):
        if fp.name == "architectures.json":
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8-sig"))
            items.append({
                "id": data.get("id", fp.stem),
                "名称": data.get("名称") or data.get("name", fp.stem),
                "描述": (data.get("描述") or data.get("description", ""))[:80],
                "文件": fp.name,
                "content_layer": data.get("content_layer") or data.get("层级", "shortform"),
            })
        except Exception:
            continue
    _INDEX_FILE.write_text(
        json.dumps({"architectures": items, "updated": __import__("time").strftime("%Y-%m-%d %H:%M:%S")},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return items


def available_architectures() -> list[dict]:
    """返回所有可用架构的元信息列表。"""
    return _load_index()


def load_architecture(arch_id: str) -> dict | None:
    """按 id 加载单个架构完整档案。"""
    index = _load_index()
    target = None
    for item in index:
        if item.get("id") == arch_id:
            target = item.get("文件")
            break
    if not target:
        # 兜底：直接按文件名找
        fp = ARCH_DIR / f"{arch_id}.json"
        if fp.exists():
            target = fp.name
        else:
            return None
    fp = ARCH_DIR / target
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


# ───────────────────────── 特征提取 ─────────────────────────

def extract_features(segments: list[dict], semantic_doc: dict | None = None,
                     meta: dict | None = None) -> dict:
    """从转写片段+语义档案中提取用于架构匹配的特征向量。

    返回特征字典：
      - 总时长秒
      - 句子数
      - 平均句长秒
      - 内容类型关键词（从文本提取）
      - 是否含问句（问答/悬念型信号）
      - 是否含对比词（对比测评型信号）
      - 是否含数字/步骤词（教程型信号）
      - 是否含情绪词（情绪故事型信号）
      - 是否含第一人称经历（vlog/故事型信号）
      - 关键信息点数量
      - 前3秒文本（钩子区域）
    """
    features: dict[str, Any] = {
        "总时长秒": 0.0,
        "句子数": 0,
        "平均句长秒": 0.0,
        "问句数": 0,
        "对比词命中": [],
        "数字步骤词命中": [],
        "情绪词命中": [],
        "第一人称命中": False,
        "关键信息点数": 0,
        "前3秒文本": "",
        "全文本": "",
    }
    if not segments:
        return features

    total_dur = 0.0
    all_texts: list[str] = []
    q_count = 0
    for seg in segments:
        t = str(seg.get("text", "")).strip()
        if not t:
            continue
        s = float(seg.get("start", 0.0))
        e = float(seg.get("end", s))
        dur = max(0.0, e - s)
        total_dur += dur
        all_texts.append(t)
        if re.search(r"[？?]|吗|呢|吧|难道|怎么|什么|为什么|哪|几|多少", t):
            q_count += 1
        # 前3秒文本
        if s < 3.0:
            features["前3秒文本"] += t

    features["总时长秒"] = round(total_dur, 2)
    features["句子数"] = len(all_texts)
    features["平均句长秒"] = round(total_dur / max(1, len(all_texts)), 2)
    features["问句数"] = q_count
    full = "".join(all_texts)
    features["全文本"] = full

    # 对比词
    contrast_words = ["对比", "vs", "VS", "区别", "不同", "哪个好", "pk", "PK",
                      "before", "after", "前后", "之前", "之后", "横评", "测评"]
    features["对比词命中"] = [w for w in contrast_words if w in full]

    # 数字/步骤词
    step_words = ["第一步", "第二步", "第三步", "首先", "然后", "接着", "最后",
                  "1.", "2.", "3.", "第一", "第二", "第三", "教程", "怎么弄", "如何"]
    features["数字步骤词命中"] = [w for w in step_words if w in full]

    # 情绪词
    emo_words = ["心疼", "感动", "哭了", "暖心", "治愈", "崩溃", "焦虑", "害怕",
                 "担心", "幸福", "开心", "难过", "委屈", "后悔", "庆幸"]
    features["情绪词命中"] = [w for w in emo_words if w in full]

    # 第一人称经历
    features["第一人称命中"] = bool(re.search(r"我(当时|那天|曾经|以前|之前|的|是|有|在|去|做|想|觉得)", full))

    # 语义档案补充
    if semantic_doc:
        pts = semantic_doc.get("关键信息点") or []
        features["关键信息点数"] = len(pts)
        what = semantic_doc.get("讲了什么") or ""
        if what and not features["全文本"]:
            features["全文本"] = what

    return features


# ───────────────────────── 架构匹配 ─────────────────────────

# 规则匹配关键词表（与 plan.py 的 GUESS 类似，但更细粒度）
_RULE_MATCH: list[tuple[str, str]] = [
    # 问答悬念型
    (r"三问|测一测|測一測|三問|你知道吗|你知道嗎|猜一猜|考考你|几个问题|幾個問題|第.问|第.個問題|第.个问题", "wenda-xuannian"),
    (r"[？?].*[？?]|吗.*呢|怎么.*什么", "wenda-xuannian"),
    # 对比测评型
    (r"对比|测评|横评|区别|哪个好|pk|PK|vs|VS|前后|之前.*之后", "duibi-ceping-arch"),
    # 教程教学型
    (r"教程|怎么弄|如何|第一步|第二步|步骤|教你|学会|方法", "jiaocheng-jiaoxue"),
    # 情绪故事型
    (r"心疼|感动|哭了|治愈|崩溃|焦虑|那天|当时|曾经|经历|故事", "qingxu-gushi-arch"),
    # 探店空间型
    (r"探店|带你看|房间|房型|环境|空间|参观|打卡", "tandian-kongjian"),
    # 卡点混剪型
    (r"卡点|混剪|节奏|炫|燃|高能|名场面", "kadian-hunjian"),
    # 剧情段子型
    (r"段子|搞笑|反转|套路|万万没想到|结局|笑死", "juqing-duanzi"),
    # 美食制作型
    (r"美食|好吃|做法| recipe |做菜|烘焙|试吃|探店.*吃", "meishi-zuozhi"),
    # 盘点合集型
    (r"盘点|合集|TOP|top|排行榜|推荐|必看|清单", "pandian-heji"),
    # vlog日常型
    (r"vlog|日常|一天|记录|生活|随拍", "vlog-richang"),
    # 口播知识型（默认兜底）
    (r"知识|干货|科普|讲解|分享|注意|提醒|建议", "koubo-zhishi-arch"),
    # ── 长视频架构规则（仅在 longform/film-level 层生效）──
    # 纪录片叙事型
    (r"纪录片|纪实|解说|史料|深度解析|真实记录|历史事件", "docu-story"),
    # 长Vlog叙事型
    (r"长vlog|一整天|多线程|旅行vlog|30天挑战|沉浸式记录|全天记录", "long-vlog"),
    # 知识课程长视频型
    (r"课程|讲座|公开课|系统学习|完整教程|章节|知识点|原理推导", "course-longform"),
    # 中视频叙事弧型
    (r"专访|人物故事|微纪录片|深度测评|完整记录|3到10分钟", "midvideo-arc"),
    # 系列章节化型
    (r"系列|连载|第.集|第.期|EP|合集|养成|持续更新|上一集", "series-chapter"),
    # 直播切片整理型
    (r"直播切片|高光片段|精彩回放|播客切片|直播剪辑|名场面切片", "live-clip"),
]


def _rule_match(full_text: str, content_layer: str | None = None) -> str | None:
    """基于关键词的规则匹配，返回架构 id 或 None。
    content_layer: 若指定，则只返回对应层级的架构（通过加载架构校验 content_layer 字段）。
    """
    for pattern, arch_id in _RULE_MATCH:
        if re.search(pattern, full_text, re.I):
            # 层级兼容性检查：加载架构，校验其 content_layer
            if content_layer:
                arch = load_architecture(arch_id)
                if arch:
                    arch_layer = arch.get("content_layer", arch.get("层级", "shortform"))
                    # shortform 架构只在 shortform 层生效；longform/film-level 架构在 longform/film-level 层生效
                    if content_layer == "shortform" and arch_layer not in ("shortform",):
                        continue
                    if content_layer in ("longform", "film-level") and arch_layer not in ("longform", "film-level"):
                        continue
            return arch_id
    return None


def _score_architecture(arch: dict, features: dict) -> float:
    """给单个架构打分，基于特征匹配度。0-100。"""
    score = 0.0
    criteria = arch.get("匹配规则") or arch.get("适用素材特征") or {}
    if not isinstance(criteria, dict):
        return 30.0  # 无匹配规则给基础分

    full_text = features.get("全文本", "")

    # 关键词命中
    keywords = criteria.get("匹配关键词") or []
    if keywords:
        hits = sum(1 for kw in keywords if kw in full_text)
        score += min(hits / max(1, len(keywords)), 1.0) * 40

    # 内容类型匹配
    content_types = criteria.get("内容类型") or []
    if content_types:
        # 简单检查：文本中是否出现内容类型相关词
        type_score = 0
        for ct in content_types:
            if ct in full_text or ct in (features.get("前3秒文本", "")):
                type_score += 1
        score += min(type_score / max(1, len(content_types)), 1.0) * 20

    # 问句特征匹配（问答悬念型需要问句）
    if criteria.get("需要问句") and features.get("问句数", 0) > 0:
        score += 15
    if criteria.get("需要问句") is False and features.get("问句数", 0) == 0:
        score += 10

    # 对比词匹配
    if criteria.get("需要对比词") and features.get("对比词命中"):
        score += 15
    if criteria.get("需要对比词") is False and not features.get("对比词命中"):
        score += 5

    # 步骤词匹配
    if criteria.get("需要步骤词") and features.get("数字步骤词命中"):
        score += 15
    if criteria.get("需要步骤词") is False and not features.get("数字步骤词命中"):
        score += 5

    # 情绪词匹配
    if criteria.get("需要情绪词") and features.get("情绪词命中"):
        score += 10

    # 关键信息点数量范围
    kp_min = criteria.get("关键信息点数量_min", 0)
    kp_max = criteria.get("关键信息点数量_max", 999)
    kp_count = features.get("关键信息点数", 0)
    if kp_min <= kp_count <= kp_max:
        score += 5

    return round(min(score, 100.0), 1)


def match_architecture(segments: list[dict], semantic_doc: dict | None = None,
                       meta: dict | None = None, top_k: int = 3,
                       content_layer: str | None = None) -> dict:
    """匹配最适合的内容架构（按内容层级选匹配池）。

    content_layer: "shortform" | "longform" | "film-level" | None（自动检测）
    shortform 匹配 13 类短视频架构；longform 匹配长视频架构；film-level 优先电影叙事结构。

    返回：
      {
        "matched": {"id":..., "名称":..., "得分":...},
        "candidates": [{"id":..., "名称":..., "得分":...}, ...],
        "features": {...},
        "content_layer": "shortform"|"longform"|"film-level",
        "method": "rule" | "score" | "fallback"
      }
    """
    features = extract_features(segments, semantic_doc, meta)
    full_text = features.get("全文本", "")
    # 自动检测内容层级
    if content_layer is None:
        content_layer = detect_content_layer(segments, meta)

    # 1. 先试规则匹配（快、准，按层级过滤）
    rule_id = _rule_match(full_text, content_layer)
    if rule_id:
        arch = load_architecture(rule_id)
        if arch:
            score = _score_architecture(arch, features)
            return {
                "matched": {"id": rule_id, "名称": arch.get("名称", rule_id), "得分": score},
                "candidates": [{"id": rule_id, "名称": arch.get("名称", rule_id), "得分": score}],
                "features": features,
                "content_layer": content_layer,
                "method": "rule",
            }

    # 2. 全量打分（按内容层级过滤匹配池）
    index = _load_index()
    scored: list[dict] = []
    for item in index:
        # 按层级过滤：shortform 架构只在 shortform 层匹配，longform 同理
        item_layer = item.get("content_layer", "shortform")
        if content_layer == "shortform" and item_layer not in ("shortform",):
            continue
        if content_layer == "longform" and item_layer not in ("longform", "film-level"):
            continue
        # film-level 优先 film-level 架构，其次 longform
        arch = load_architecture(item["id"])
        if not arch:
            continue
        s = _score_architecture(arch, features)
        # 层级匹配加分
        if item_layer == content_layer:
            s += 10
        scored.append({"id": item["id"], "名称": item.get("名称", item["id"]), "得分": s})
    scored.sort(key=lambda x: x["得分"], reverse=True)

    if scored and scored[0]["得分"] >= 20:
        return {
            "matched": scored[0],
            "candidates": scored[:top_k],
            "features": features,
            "content_layer": content_layer,
            "method": "score",
        }

    # 3. 兜底：口播知识型
    fallback = load_architecture("koubo-zhishi-arch")
    fallback_name = fallback.get("名称", "口播知识型") if fallback else "口播知识型"
    # fallback 按层级选默认架构
    fallback_id = "koubo-zhishi-arch"
    fallback_name = "口播知识型"
    if content_layer == "longform":
        fb_arch = load_architecture("docu-story")
        if fb_arch:
            fallback_id = "docu-story"
            fallback_name = fb_arch.get("名称", "纪录片叙事型")
    if content_layer == "film-level":
        fb_arch = load_architecture("film-three-act")
        if fb_arch:
            fallback_id = "film-three-act"
            fallback_name = fb_arch.get("名称", "电影三幕结构")
    return {
        "matched": {"id": fallback_id, "名称": fallback_name, "得分": 10.0},
        "candidates": [{"id": fallback_id, "名称": fallback_name, "得分": 10.0}],
        "features": features,
        "content_layer": content_layer,
        "method": "fallback",
    }


# ───────────────────────── 架构+风格联合决策 ─────────────────────────

def combine_with_style(architecture_match: dict, style_card: dict | None = None,
                       film_language: dict | None = None,
                       platform: str | None = None) -> dict:
    """将架构决策与风格卡 + 电影剪辑语言联合，生成完整的剪辑决策输入。

    架构决定「讲什么结构」（段落序列/钩子/CTA/信息密度），
    风格决定「怎么剪」（转场/字幕/BGM/滤镜/节奏参数），
    电影语言决定「用什么镜头语言和剪辑手法」（景别/机位/剪辑点/蒙太奇/转场叙事功能）。
    三者联合后注入 edit_intent，让 LLM 在生成逐句意图时同时参考结构约束、视听参数和镜头语言。
    """
    arch_id = (architecture_match.get("matched") or {}).get("id", "")
    arch = load_architecture(arch_id) if arch_id else None

    content_layer = architecture_match.get("content_layer", "shortform")
    decision: dict[str, Any] = {
        "architecture": {
            "id": arch_id,
            "名称": (architecture_match.get("matched") or {}).get("名称", ""),
            "匹配方法": architecture_match.get("method", ""),
            "匹配得分": (architecture_match.get("matched") or {}).get("得分", 0),
            "content_layer": content_layer,
        },
        "style": None,
        "film_language": film_language or {},
        "联合约束": {},
    }

    if arch:
        decision["architecture"]["段落序列"] = arch.get("段落序列", [])
        decision["architecture"]["钩子规则"] = arch.get("钩子规则", {})
        decision["architecture"]["CTA规则"] = arch.get("CTA规则", {})
        decision["architecture"]["信息密度目标"] = arch.get("信息密度目标", "")
        decision["architecture"]["与styles映射"] = arch.get("与styles映射", [])

    if style_card:
        decision["style"] = {
            "id": style_card.get("id", ""),
            "名称": style_card.get("名称", ""),
            "剪辑参数": style_card.get("剪辑参数", {}),
        }

    # 联合约束：架构的段落节奏 × 风格的镜头参数
    joint: dict[str, Any] = {}
    if arch and style_card:
        params = style_card.get("剪辑参数") or {}
        segments = arch.get("段落序列") or []
        # 为每个段落计算建议镜头长度
        joint["段落镜头建议"] = []
        for seg in segments:
            rhythm = seg.get("节奏", "中")
            base_min = float(params.get("镜头最短秒", 2.0))
            base_max = float(params.get("镜头最长秒", 6.0))
            if rhythm == "快":
                shot_range = (round(base_min * 0.6, 1), round(base_max * 0.7, 1))
            elif rhythm == "慢":
                shot_range = (round(base_min * 1.5, 1), round(base_max * 1.8, 1))
            else:
                shot_range = (base_min, base_max)
            joint["段落镜头建议"].append({
                "段落": seg.get("功能", ""),
                "节奏": rhythm,
                "建议镜头长度秒": shot_range,
            })
        # BGM 联合：架构的情绪分段 × 风格的 BGM 情绪
        joint["BGM联合"] = {
            "风格BGM情绪": params.get("BGM情绪", ""),
            "风格BGM音量": params.get("BGM音量", 0.15),
            "架构情绪分段": [{"段落": s.get("功能", ""), "情绪": s.get("情绪", "")}
                            for s in segments if s.get("情绪")],
        }
    # 电影语言联合约束：按段落功能推荐景别/剪辑点/转场
    if film_language:
        joint["电影语言建议"] = _film_language_for_segments(
            film_language, arch.get("段落序列") or [], content_layer)
    # RAG 召回：按素材文本召回相关架构段落规则注入上下文（懒加载，避免循环依赖）
    try:
        from . import arch_rag as _rag
        query_bits = []
        feats = architecture_match.get("features") or {}
        if feats.get("前3秒文本"):
            query_bits.append(str(feats["前3秒文本"])[:80])
        if feats.get("全文本"):
            query_bits.append(str(feats["全文本"])[:200])
        if arch_id:
            query_bits.append(arch_id)
        rag_query = " ".join(query_bits) or arch_id
        joint["rag_context"] = _rag.get_rag_context(rag_query, content_layer=content_layer, top_k=3)
    except Exception as exc:  # noqa: BLE001
        joint["rag_context"] = ""
        joint["rag_error"] = str(exc)[:120]
    # 平台校准联合（v1.0）：架构 + 风格 + 电影语言 + 目标平台 四方决策
    if platform:
        try:
            from . import platform_calibrator as _pc
            _cal = _pc.get_calibration(platform)
            decision["platform"] = {
                "platform_id": _cal["platform_id"],
                "名称": _cal.get("平台名称"),
                "画幅": _cal.get("画幅"),
                "平均镜头时长目标": _cal.get("平均镜头时长目标"),
                "钩子密度": _cal.get("钩子密度"),
                "首钩位置": _cal.get("首钩位置"),
                "BGM音量": _cal.get("BGM音量"),
                "字幕风格": _cal.get("字幕风格"),
                "推荐时长甜点区": _cal.get("推荐甜点区"),
            }
            joint["平台校准"] = decision["platform"]
        except Exception as _pe:  # noqa: BLE001
            decision["platform"] = {"error": str(_pe)[:160], "platform_id": platform}
    decision["联合约束"] = joint
    return decision


# ───────────────────────── 人可读输出 ─────────────────────────

def architecture_decision_sheet(decision: dict) -> str:
    """把架构+风格联合决策转成人可读的决策单。"""
    if not decision:
        return "（无架构决策数据）"
    arch = decision.get("architecture") or {}
    out: list[str] = []
    out.append(f"内容架构：{arch.get('名称') or arch.get('id') or '?'}（id={arch.get('id', '?')}）")
    out.append(f"匹配方法：{arch.get('匹配方法', '?')}，得分：{arch.get('匹配得分', '?')}")

    segments = arch.get("段落序列") or []
    if segments:
        out.append("段落序列：")
        for i, seg in enumerate(segments, 1):
            out.append(f"  {i}. {seg.get('功能', '?')}｜时长占比 {seg.get('时长占比', '?')}｜"
                       f"信息密度 {seg.get('信息密度', '?')}｜节奏 {seg.get('节奏', '?')}"
                       + (f"｜{seg.get('描述', '')}" if seg.get("描述") else ""))

    hook = arch.get("钩子规则") or {}
    if hook:
        out.append(f"钩子规则：{hook.get('类型', '?')}｜{hook.get('策略', '')}")
    cta = arch.get("CTA规则") or {}
    if cta:
        out.append(f"CTA规则：{cta.get('类型', '?')}｜{cta.get('策略', '')}")

    style = decision.get("style") or {}
    if style:
        out.append(f"联合风格：{style.get('名称') or style.get('id', '?')}")

    joint = decision.get("联合约束") or {}
    if joint.get("段落镜头建议"):
        out.append("段落镜头建议：")
        for item in joint["段落镜头建议"]:
            rng = item.get("建议镜头长度秒", (0, 0))
            out.append(f"  {item.get('段落', '?')}（{item.get('节奏', '?')}）：{rng[0]}-{rng[1]}s")

    return "\n".join(out)


# ───────────────────────── 注入 edit_intent ─────────────────────────

def inject_into_intent(intent: dict, architecture_decision: dict) -> dict:
    """将架构决策注入 edit_intent 的输出，让后续剪辑执行层能读到结构约束。

    在 intent 顶层增加 architecture / film_language 字段，与「内容地图」「逐句意图」「节奏方案」并列。
    """
    if not intent:
        return intent
    intent["architecture"] = architecture_decision.get("architecture", {})
    intent["architecture_joint"] = architecture_decision.get("联合约束", {})
    # 电影剪辑语言横切知识
    fl = architecture_decision.get("film_language")
    if fl:
        intent["film_language"] = fl
    # 同时把架构的段落序列写进内容地图的叙事结构（兼容旧字段）
    cm = intent.get("内容地图") or {}
    segments = (architecture_decision.get("architecture") or {}).get("段落序列") or []
    if segments and not cm.get("叙事结构"):
        cm["叙事结构"] = [s.get("功能", "") for s in segments]
        intent["内容地图"] = cm
    return intent


# ═══════════════════════════════════════════════════════════════
# v2.0 扩展：三层级判定 + 电影剪辑语言横切知识
# ═══════════════════════════════════════════════════════════════

# 时长层级阈值（秒）
SHORTFORM_MAX_SEC = 180.0   # <=3min 短视频
LONGFORM_MAX_SEC = 600.0    # 3-10min 长视频；>10min film-level


def detect_content_layer(segments: list[dict], meta: dict | None = None) -> str:
    """根据素材总时长判定内容层级。

    返回：
      "shortform"  — <=3min，走 13 类短视频架构匹配
      "longform"   — 3-10min，走长视频架构匹配
      "film-level" — >10min 或多镜头叙事素材，走电影级叙事结构

    meta 可覆盖：{"content_layer": "longform"} 手动指定。
    """
    if meta and meta.get("content_layer"):
        return meta["content_layer"]
    total = 0.0
    for seg in segments or []:
        s = float(seg.get("start", 0.0))
        e = float(seg.get("end", s))
        total += max(0.0, e - s)
    # 如果 segments 没有时长信息，从 meta 取
    if total == 0.0 and meta:
        total = float(meta.get("总时长秒", 0.0) or meta.get("duration", 0.0) or 0)
    if total > LONGFORM_MAX_SEC:
        return "film-level"
    if total > SHORTFORM_MAX_SEC:
        return "longform"
    return "shortform"


def load_film_language() -> dict | None:
    """加载电影剪辑语言档案（film-language.json）。

    电影语言是横切知识，不参与架构匹配，而是在方案生成时按内容层级注入相关规则。
    """
    fp = ARCH_DIR / "film-language.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def get_relevant_film_rules(film_language: dict | None, content_layer: str,
                             architecture_id: str = "") -> dict:
    """根据内容层级和架构类型，筛选相关的电影剪辑语言规则。

    不是所有电影语言规则都适用于所有内容——短视频不需要复杂的三幕节拍表，
    电影级素材则需要完整的叙事结构和镜头语言指导。

    返回筛选后的 film_language 子集（含适用标注）。
    """
    if not film_language:
        return {}
    relevant: dict[str, Any] = {
        "content_layer": content_layer,
        "architecture_id": architecture_id,
    }

    # 剪辑理论：所有层级都适用（但 shortform 简化）
    editing = film_language.get("剪辑理论") or {}
    if editing:
        if content_layer == "shortform":
            # 短视频只保留核心剪辑手法
            relevant["剪辑理论"] = {
                k: v for k, v in editing.items()
                if k in ("硬切", "J-cut", "L-cut", "匹配剪辑", "跳切", "动作剪辑点", "音乐剪辑点")
            }
        else:
            relevant["剪辑理论"] = editing

    # 镜头语言：所有层级适用
    relevant["镜头语言"] = film_language.get("镜头语言", {})

    # 叙事结构：longform 和 film-level 适用，shortform 只保留三幕简化版
    narrative = film_language.get("叙事结构") or {}
    if narrative:
        if content_layer == "shortform":
            relevant["叙事结构"] = {"三幕结构（微缩版）": narrative.get("三幕结构", {})}
        elif content_layer == "longform":
            relevant["叙事结构"] = {
                k: v for k, v in narrative.items()
                if k in ("三幕结构", "五幕结构", "序列法")
            }
        else:  # film-level
            relevant["叙事结构"] = narrative

    # 节奏语言：所有层级适用
    relevant["节奏语言"] = film_language.get("节奏语言", {})

    # 剪映可实现性汇总
    relevant["剪映可实现性"] = film_language.get("剪映可实现性汇总", {})

    return relevant


def _film_language_for_segments(film_language: dict, segments: list[dict],
                                  content_layer: str) -> list[dict]:
    """根据段落功能，为每个段落推荐电影剪辑语言参数（景别/剪辑点/转场）。

    这是架构+电影语言的联合约束：不同段落功能需要不同的镜头语言处理。
    """
    if not film_language or not segments:
        return []
    shot_lang = film_language.get("镜头语言") or {}
    rhythm = film_language.get("节奏语言") or {}
    editing = film_language.get("剪辑理论") or {}

    # 段落功能 → 推荐景别映射
    func_shot_map = {
        "钩子": ["特写", "大特写", "近景"],
        "结论前置": ["近景", "中景"],
        "分点干货": ["中景", "近景", "特写（关键词）"],
        "总结": ["中景", "近景"],
        "CTA": ["近景", "中景"],
        "设问钩子": ["特写", "近景"],
        "误导": ["中景", "全景"],
        "揭晓": ["特写", "大特写"],
        "痛点钩子": ["特写", "近景"],
        "共鸣铺垫": ["中景", "全景"],
        "冲突升级": ["特写", "近景", "快切"],
        "转折翻盘": ["特写", "大特写", "闪白"],
        "升华": ["全景", "远景", "慢镜头"],
        "结果前置": ["特写", "大特写"],
        "分步演示": ["近景", "特写（手部/操作）"],
        "易错点": ["大特写", "慢动作"],
        "结论钩子": ["特写", "近景"],
        "动线环境": ["全景", "远景", "广角"],
        "核心亮点": ["特写", "近景", "微距"],
        "前奏铺垫": ["全景", "远景", "慢镜头"],
        "主歌": ["中景", "近景", "快切"],
        "副歌": ["特写", "快切", "闪白"],
        "日常钩子": ["中景", "近景"],
        "铺垫": ["中景", "全景"],
        "反转": ["特写", "大特写", "骤停"],
        "成品钩子": ["大特写", "微距", "慢动作"],
        "制作过程": ["近景", "特写（手部）"],
        "引入钩子": ["中景", "近景"],
        "逐条盘点": ["中景", "近景", "产品特写"],
        "身份钩子": ["中景", "近景"],
        "时段碎片": ["全景", "中景", "快切"],
        "摩擦点": ["特写", "近景"],
        "引片": ["中景", "近景"],
        "论证": ["中景", "近景", "快切"],
        "Hook cue": ["特写", "快切"],
        "Action": ["中景", "近景", "卡点"],
    }

    # 段落功能 → 推荐剪辑点
    func_cut_map = {
        "钩子": "音乐剪辑点（重拍切入）",
        "冲突升级": "情绪剪辑点（呼吸点切入）",
        "转折翻盘": "动作剪辑点（动作峰值切入）+ 闪白",
        "升华": "情绪剪辑点（长镜头留白）",
        "揭晓": "音乐剪辑点（重音爆点）",
        "副歌": "音乐剪辑点（每拍切入）",
        "反转": "动作剪辑点 + 骤停",
        "易错点": "慢动作 + 大特写",
    }

    result = []
    for seg in segments:
        func = seg.get("功能", "")
        rhythm_seg = seg.get("节奏", "中")
        item = {
            "段落": func,
            "推荐景别": func_shot_map.get(func, ["中景", "近景"]),
            "推荐剪辑点": func_cut_map.get(func, "动作剪辑点或硬切"),
            "推荐转场": _recommend_transition(func, rhythm_seg, editing),
        }
        result.append(item)
    return result


def _recommend_transition(func: str, rhythm: str, editing: dict) -> str:
    """根据段落功能和节奏推荐转场类型。"""
    # 高潮/转折用闪白
    if func in ("转折翻盘", "揭晓", "反转", "副歌"):
        return "闪白（0.3-0.5s）"
    # 情绪/回忆用叠化
    if func in ("升华", "共鸣铺垫", "前奏铺垫", "收尾回落"):
        return "叠化（1-2s）"
    # 时间/空间转换用淡入淡出
    if func in ("时段碎片", "动线环境", "身份+场景"):
        return "淡入淡出（0.5-1s）"
    # 快节奏用硬切
    if rhythm in ("快", "最快") or func in ("主歌", "逐条盘点", "分点干货"):
        return "硬切（0s）"
    # 默认硬切
    return "硬切（0s）"


def film_language_decision_sheet(film_language: dict | None) -> str:
    """把电影剪辑语言决策转成人可读的摘要。"""
    if not film_language:
        return "（无电影剪辑语言数据）"
    out: list[str] = []
    out.append(f"内容层级：{film_language.get('content_layer', '?')}")
    if film_language.get("architecture_id"):
        out.append(f"关联架构：{film_language['architecture_id']}")
    for category in ("剪辑理论", "镜头语言", "叙事结构", "节奏语言"):
        items = film_language.get(category) or {}
        if items:
            out.append(f"{category}（{len(items)} 项）：" + "、".join(str(k) for k in list(items.keys())[:8]))
    return "\n".join(out)
