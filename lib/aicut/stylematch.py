r"""风格匹配决策模块（v3.5 风格引擎 · 深度优化版）。

给一段素材的语义档案 / shotprof 摘要 / 转写文本，从风格知识库里挑出最合适的
Top3 剪辑风格，并给出置信度与理由。两条通道：

  · 规则通道（优先，省算力）：按语义档案字段打分（结构/情绪/信息点数量/关键词），
    新增内容类型自动检测 + 负向惩罚，置信度用 softmax(temperature=0.5) 拉开区分度。
  · Ollama 本地决策通道：规则 Top1 置信度 < 0.6 时触发，让本地模型再看一眼。

不引入付费 API；Ollama 不可用时静默回退规则结果。

对外接口：
    match_style(semantic_doc, shotprof_summary, transcript_text, style_registry_path)
    -> {"推荐": [{"id","名称","置信度","理由","差异化"}],
        "匹配方式": "rule|ollama|fallback",
        "使用的输入": "semantic|shotprof|transcript",
        "间隔": Top1-Top2 置信度差, "并列推荐": bool}
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "content-library" / "styles.json"

# 规则打分权重（语义通道）
W_STRUCT = 30      # 适合结构命中
W_MOOD = 25        # 情绪基调命中
W_INFO_COUNT = 20  # 关键信息点数量落在风格区间
W_CONTENT_TYPE = 10  # 内容类型关键词
W_PROTAGONIST = 10   # 主角类型关键词
W_SCENE = 10         # 场景类型关键词

# 深度优化：内容类型自动检测 + 负向惩罚
W_DETECT_BONUS = 25   # 内容类型命中对应风格
W_DETECT_PENALTY = -10  # 内容类型不匹配的其他风格
P_MOOD_MISS = -15     # 情绪基调不匹配
P_COUNT_OUT = -10     # 关键信息点数量超出区间

CONF_OLLAMA_THRESHOLD = 0.6
SOFTMAX_T = 0.5          # softmax 温度（越小越陡峭，区分度越高）
TIE_GAP = 0.05           # Top1-Top2 间隔小于此值视为并列

# 内容类型自动检测：关键词 -> 优先风格
CONTENT_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("jiaocheng-buzhou", ["第一步", "第二步", "第三步", "步骤", "教你", "怎么", "方法", "跟着做", "演示", "教程"]),
    ("duibi-ceping", ["对比", "区别", "哪个好", "vs", "VS", "前后", "哪个更", "还是"]),
    ("qingxu-gushi", ["那天", "我记得", "经历", "故事", "当时", "我以前", "我之前"]),
    ("kongjian-zhanshi", ["带大家看", "参观", "环境", "房间", "设施", "探店", "房型", "看一下"]),
    ("zhiyu-shuhuan", ["温暖", "陪伴", "时光", "慢慢", "安静", "治愈", "舒缓", "放松"]),
    ("xuanchuan-pian", ["品牌", "专业团队", "实力", "选择我们", "我们月子中心", "成立", "连锁"]),
    ("zhuanti-pandian", ["三个", "五种", "十大", "盘点", "汇总", "几个", "误区", "技巧", "合集"]),
    ("fangtan-duitan", ["采访", "对话", "问答", "请教", "请问", "专家说"]),
]
# 盘点类同时利好图文快闪
DETECT_SECONDARY = {"zhuanti-pandian": ("wenzi-kuaishan", 15)}


# --------------------------------------------------------------------------- #
# 风格注册表加载
# --------------------------------------------------------------------------- #
def load_registry(style_registry_path: Path | str | None = None) -> list[dict]:
    """读 styles.json 索引；每条尽量合并 per-style 完整卡。"""
    idx_path = Path(style_registry_path) if style_registry_path else DEFAULT_REGISTRY
    if not idx_path.is_absolute():
        idx_path = ROOT / idx_path
    data = json.loads(idx_path.read_text(encoding="utf-8-sig"))
    out: list[dict] = []
    for entry in data.get("styles", []):
        card = dict(entry)
        rel = entry.get("文件")
        if rel:
            p = idx_path.parent / rel
            if not p.exists():
                p = idx_path.parent / rel
            if p.exists():
                try:
                    full = json.loads(p.read_text(encoding="utf-8-sig"))
                    full.setdefault("文件", rel)
                    card = full
                except Exception:  # noqa: BLE001
                    card = dict(entry)
        out.append(card)
    return out


def list_style_ids(style_registry_path: Path | str | None = None) -> list[str]:
    return [c["id"] for c in load_registry(style_registry_path)]


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [x for x in str(v).replace("、", "/").replace("，", "/").replace(",", "/").split("/") if x]


def _semantic_text(semantic: dict) -> str:
    parts = []
    for key in ("讲了什么", "场景"):
        if semantic.get(key):
            parts.append(str(semantic[key]))
    for key in ("叙事摘要", "关键信息点"):
        for item in semantic.get(key) or []:
            parts.append(str(item))
    return " ".join(parts)


def _mood_overlap(need: list[str], have: list[str]) -> bool:
    need_set = set(need)
    return any(any(n in h or h in n for n in need_set) for h in have)


def detect_content_type(text: str) -> tuple[str | None, str]:
    """从文本里识别内容类型，返回 (命中风格id, 命中关键词)。无命中返回 (None, "")。"""
    best_id, best_hits, best_kw = None, 0, ""
    for sid, kws in CONTENT_TYPE_RULES:
        hits = [k for k in kws if k in text]
        if len(hits) > best_hits:
            best_id, best_hits, best_kw = sid, len(hits), hits[0]
    return (best_id if best_hits >= 1 else None), best_kw


def softmax(values: list[float], t: float = SOFTMAX_T) -> list[float]:
    """对一组分数做 softmax(temperature=t)；负值先截断到 0。返回和为 1 的概率。"""
    arr = [max(0.0, float(v)) for v in values]
    if not arr or sum(arr) <= 0:
        n = max(1, len(arr))
        return [1.0 / n] * len(arr)
    m = max(arr)
    exps = [math.exp((v - m) / t) for v in arr]
    s = sum(exps)
    return [e / s for e in exps]


# --------------------------------------------------------------------------- #
# 规则打分：语义通道
# --------------------------------------------------------------------------- #
def _score_by_semantic(card: dict, semantic: dict,
                      detected_id: str | None, detected_kw: str) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    feat = card.get("适用素材特征") or {}
    cid = card.get("id")

    # 内容类型自动检测：命中 +25，其他风格 -10
    if detected_id:
        if cid == detected_id:
            score += W_DETECT_BONUS
            reasons.append(f"内容类型检测「{detected_kw}」-> 直接命中")
        else:
            score += W_DETECT_PENALTY

    # 适合结构
    want_struct = str(semantic.get("适合结构") or "").strip()
    if want_struct:
        struct_list = list(feat.get("适合结构") or [])
        if want_struct == card.get("结构") or want_struct == cid or want_struct in struct_list:
            score += W_STRUCT
            reasons.append(f"适合结构「{want_struct}」匹配")

    # 情绪基调：命中 +25，不匹配 -15
    moods = _as_list(semantic.get("情绪基调"))
    if moods and feat.get("情绪基调"):
        if _mood_overlap(list(feat["情绪基调"]), moods):
            score += W_MOOD
            reasons.append(f"情绪基调 {moods} 契合")
        else:
            score += P_MOOD_MISS
            reasons.append(f"情绪基调 {moods} 不匹配该风格")

    # 关键信息点数量：落区间 +20，超出区间 -10
    info = semantic.get("关键信息点") or []
    n_info = len(info) if isinstance(info, (list, tuple)) else 0
    lo, hi = feat.get("关键信息点数量_min"), feat.get("关键信息点数量_max")
    if lo is not None and hi is not None and n_info:
        if int(lo) <= n_info <= int(hi):
            score += W_INFO_COUNT
            reasons.append(f"{n_info} 个关键信息点在区间[{lo}-{hi}]")
        else:
            score += P_COUNT_OUT
            reasons.append(f"{n_info} 个关键信息点超出区间[{lo}-{hi}]")

    # 内容类型 / 主角 / 场景 关键词
    blob = _semantic_text(semantic)
    if feat.get("内容类型") and any(kw in blob for kw in feat["内容类型"]):
        score += W_CONTENT_TYPE
        hit = [kw for kw in feat["内容类型"] if kw in blob]
        reasons.append(f"内容类型含「{hit[0]}」")
    protag = str(semantic.get("主角") or "")
    if feat.get("主角类型") and any(kw in protag for kw in feat["主角类型"]):
        score += W_PROTAGONIST
        hit = [kw for kw in feat["主角类型"] if kw in protag]
        reasons.append(f"主角属 {hit[0]}")
    scene = str(semantic.get("场景") or "")
    if feat.get("场景类型") and any(kw in scene for kw in feat["场景类型"]):
        score += W_SCENE
        hit = [kw for kw in feat["场景类型"] if kw in scene]
        reasons.append(f"场景含 {hit[0]}")

    return score, reasons


# --------------------------------------------------------------------------- #
# 规则打分：降级通道（无语义档案，只有 shotprof summary + 转写）
# --------------------------------------------------------------------------- #
def _score_by_degraded(card: dict, shotprof_summary: dict, transcript_text: str,
                       detected_id: str | None, detected_kw: str) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    feat = card.get("适用素材特征") or {}
    text = (transcript_text or "").strip()
    tlen = len(text)
    cid = card.get("id")

    if detected_id:
        if cid == detected_id:
            score += W_DETECT_BONUS
            reasons.append(f"内容类型检测「{detected_kw}」-> 直接命中")
        else:
            score += W_DETECT_PENALTY

    keywords = list(card.get("适用") or []) + list(feat.get("内容类型") or [])
    hits = [kw for kw in keywords if kw and kw in text]
    if hits:
        score += W_CONTENT_TYPE
        reasons.append(f"转写命中关键词「{hits[0]}」")

    hooks = (shotprof_summary or {}).get("hook_candidates") or []
    try:
        n_hooks = len(hooks)
    except TypeError:
        n_hooks = 0
    max_shot = float((card.get("剪辑参数") or {}).get("镜头最长秒") or 4.0)
    if n_hooks >= 2 and max_shot <= 3.0:
        score += 15
        reasons.append(f"{n_hooks} 个钩子候选，适合快节奏")
    elif n_hooks == 1 and max_shot <= 5.0:
        score += 8
        reasons.append("有 1 个钩子候选")

    conflicts = (shotprof_summary or {}).get("safe_area_conflicts") or []
    try:
        n_conf = len(conflicts)
    except TypeError:
        n_conf = 0
    if n_conf >= 2 and max_shot <= 2.0:
        score -= 10
        reasons.append(f"{n_conf} 处安全区冲突，不利文字快闪")

    if tlen:
        if tlen < 40 and max_shot <= 3.0:
            score += 8
            reasons.append("转写短，偏展示/探店")
        elif tlen >= 120 and max_shot >= 3.0:
            score += 8
            reasons.append("转写长，信息量大，偏讲解/教程")

    return score, reasons


# --------------------------------------------------------------------------- #
# Ollama 本地决策通道
# --------------------------------------------------------------------------- #
def _ollama_decide(semantic: dict | None, shotprof_summary: dict | None,
                   transcript_text: str, cards: list[dict]) -> dict | None:
    """让本地 Ollama 从候选风格里挑一个并给理由。失败返回 None。"""
    try:
        from . import llm  # type: ignore
    except Exception:  # noqa: BLE001
        try:
            import llm  # type: ignore
        except Exception:  # noqa: BLE001
            return None

    snap: dict = {"输入": {}}
    if semantic:
        snap["输入"]["语义档案"] = {
            "主角": semantic.get("主角"), "场景": semantic.get("场景"),
            "讲了什么": semantic.get("讲了什么") or semantic.get("叙事摘要"),
            "关键信息点": semantic.get("关键信息点"),
            "情绪基调": semantic.get("情绪基调"),
            "适合结构": semantic.get("适合结构"),
        }
    if shotprof_summary:
        snap["输入"]["镜头摘要"] = {
            "钩子候选数": len(shotprof_summary.get("hook_candidates") or []),
            "安全区冲突数": len(shotprof_summary.get("safe_area_conflicts") or []),
            "提示": shotprof_summary.get("hints"),
        }
    if transcript_text:
        snap["输入"]["转写片段"] = transcript_text[:300]

    catalog = [{"id": c.get("id"), "名称": c.get("名称"),
                 "适用": c.get("适用"), "特征": c.get("适用素材特征")} for c in cards]
    from .learning_context import retrieve, prompt_context
    learned = prompt_context(retrieve(transcript_text + ' ' + _semantic_text(semantic or {})
                                      + ' 风格 结构', budget=5000, top_k=6))
    prompt = (
        "你是短视频剪辑风格决策助手。根据下面素材特征，从候选风格里选最合适的 1 个。\n"
        "只输出 JSON：{\"id\": \"风格id\", \"理由\": \"二十字内\"}\n\n"
        "素材：\n" + json.dumps(snap, ensure_ascii=False) +
        "\n\n候选风格：\n" + json.dumps(catalog, ensure_ascii=False) + learned
    )
    try:
        out = llm.json_chat(
            [{"role": "user", "content": prompt}],
            provider="ollama", temperature=0.2, timeout=120)
    except Exception:  # noqa: BLE001
        return None
    sid = str(out.get("id") or "").strip()
    if not sid or sid not in {c.get("id") for c in cards}:
        return None
    return {"id": sid, "理由": str(out.get("理由") or "本地模型推荐")}


# --------------------------------------------------------------------------- #
# 主接口
# --------------------------------------------------------------------------- #
def match_style(semantic_doc: dict | None = None,
                shotprof_summary: dict | None = None,
                transcript_text: str = "",
                style_registry_path: Path | str | None = None) -> dict:
    """返回 Top3 推荐 + softmax 置信度 + 理由 + 区分度。"""
    cards = load_registry(style_registry_path)
    use_semantic = bool(semantic_doc)
    use_input = "semantic" if use_semantic else ("shotprof" if shotprof_summary else "transcript")

    # 内容类型自动检测：语义文本 + 转写合并
    combined = _semantic_text(semantic_doc or {}) + " " + (transcript_text or "")
    detected_id, detected_kw = detect_content_type(combined)

    scored: list[tuple[dict, float, list[str]]] = []
    for card in cards:
        if use_semantic:
            s, rs = _score_by_semantic(card, semantic_doc, detected_id, detected_kw)
        else:
            s, rs = _score_by_degraded(card, shotprof_summary or {}, transcript_text or "",
                                       detected_id, detected_kw)
        scored.append((card, s, rs))

    scored.sort(key=lambda x: x[1], reverse=True)
    匹配方式 = "rule"

    # 全负/全零 → 兜底
    if max(s for _, s, _ in scored) <= 0:
        匹配方式 = "fallback"
        top3 = scored[:3]
        probs = softmax([max(0.0, s) for _, s, _ in top3])
        recs = [{
            "id": c.get("id"), "名称": c.get("名称"),
            "置信度": round(probs[i], 3), "理由": "无可用信号，按默认顺序兜底",
            "差异化": "兜底，无区分",
        } for i, (c, _, _) in enumerate(top3)]
        return {"推荐": recs, "匹配方式": 匹配方式, "使用的输入": use_input,
                "间隔": 0.0, "并列推荐": True}

    # softmax 置信度（全部 12 个风格归一化）
    all_scores = [s for _, s, _ in scored]
    probs = softmax(all_scores)

    # Top1 置信度偏低 → Ollama 本地决策通道（把模型推荐置顶，重算概率）
    top1_conf = probs[0]
    if top1_conf < CONF_OLLAMA_THRESHOLD:
        ollama = _ollama_decide(semantic_doc, shotprof_summary, transcript_text, cards)
        if ollama:
            匹配方式 = "ollama"
            oid = ollama["id"]
            scored.sort(key=lambda x: 0 if x[0].get("id") == oid else 1)
            probs = softmax([s for _, s, _ in scored])

    top3 = scored[:3]
    top3_probs = probs[:3]
    gap = round(top3_probs[0] - top3_probs[1], 3) if len(top3_probs) >= 2 else 1.0
    tie = bool(len(top3_probs) >= 2 and gap < TIE_GAP)

    recs = []
    names = {c.get("id"): c.get("名称") for c in cards}
    for rank, (card, s, rs) in enumerate(top3):
        reason = "；".join(rs) if rs else "通用候选"
        if 匹配方式 == "ollama" and rank == 0:
            reason = (reason + "；Ollama 本地决策" if reason != "通用候选" else "Ollama 本地决策")
        # 差异化理由：说明相对相邻名次的优势
        if rank == 0:
            others = [names.get(c.get("id")) for c, _, _ in top3[1:]]
            diff = f"领先 Top2「{others[0]}」{gap:.2f}：" + (rs[0] if rs else "")
        else:
            diff = f"次优候选，置信度 {top3_probs[rank]:.2f}"
        if tie and rank == 0:
            diff += "（与 Top2 接近，建议都出片对比）"
        recs.append({"id": card.get("id"), "名称": card.get("名称"),
                     "置信度": round(top3_probs[rank], 3), "理由": reason,
                     "差异化": diff})
    return {"推荐": recs, "匹配方式": 匹配方式, "使用的输入": use_input,
            "间隔": gap, "并列推荐": tie}


__all__ = ["match_style", "load_registry", "list_style_ids", "detect_content_type", "softmax"]
