"""语义 filler（口癖 / 重复口吃）去除 —— 词级时间戳两道。

两道合一：
  规则道 detect_fillers          纯词表 + 连续重复检测，零依赖、确定性；
  语义道 detect_fillers_semantic 把逐字稿喂本地 Ollama，让模型标出口误/filler，
                                  结果与规则道取并集。

默认词表是中文口播常用口癖。词级时间戳来自 faster-whisper word_timestamps；
旧逐字稿没有 words 字段时由调用方降级到句段级。
"""
from __future__ import annotations

import re

# 中文口播常用 filler（词表道）。多字词（如"你知道吗"）按连续字/词拼接匹配。
DEFAULT_FILLERS: list[str] = [
    "嗯", "啊", "呃", "哦",
    "那个", "就是", "然后", "其实",
    "怎么说", "你知道吗", "对吧", "反正",
]

# 归一化：去标点 / 空白 / 转小写（与 retake.norm 保持一致的口径）
_PUNCT_RE = re.compile(r"[\s，。、？！；：“”‘’…—·,.?!;:'\"()（）《》<>【】\[\]]")


def _norm_token(text: object) -> str:
    return _PUNCT_RE.sub("", str(text or "")).lower()


def _token_text(w: dict) -> str:
    """兼容 word / text 两种键名。"""
    return str(w.get("word") or w.get("text") or "")


def detect_fillers(
    words: list[dict],
    filler_words: list[str] | None = None,
) -> list[dict]:
    """词级 filler / 重复检测。

    输入词级时间戳：[{start, end, word}]（start/end 秒）。
    输出：[{start, end, word, type}]
      type = "filler"  命中词表（如 嗯 / 啊 / 那个）；
      type = "repeat"  连续重复相同词的前一个（口吃，如"今天今天" -> 删前一个保后一个）。

    规则：
      ① 词表命中：单个 token 或连续 token 拼接后等于某个 filler 短语；
         优先匹配最长短语（避免"你知道吗"被"吗"等短词截断）；
      ② 连续重复：tokens[i] == tokens[i+1] 且 tokens[i] 未被①覆盖时，
         把第 i 个标为 repeat（只删前一个，保后一个，避免把"今天今天"整段删没）。
    """
    fillers = list(filler_words) if filler_words is not None else list(DEFAULT_FILLERS)
    fnorm = sorted({_norm_token(x) for x in fillers if _norm_token(x)}, key=len, reverse=True)
    single = {x for x in fnorm if len(x) == 1}

    toks = [_norm_token(_token_text(w)) for w in words]
    marks: dict[tuple[int, int], dict] = {}  # (token_a, token_b_exclusive) -> 区间记录

    def _overlaps(a: int, b: int) -> bool:
        return any(not (b <= x or a >= y) for (x, y) in marks)

    i, n = 0, len(words)
    while i < n:
        hit: tuple[int, int, str, str] | None = None
        # 先尝试多字短语匹配（最长优先）
        for phrase in fnorm:
            if len(phrase) < 2:
                continue
            acc, j = "", i
            while j < n and len(acc) < len(phrase):
                acc += toks[j]
                j += 1
            if acc == phrase:
                hit = (i, j, phrase, "filler")
                break
        # 再退到单 token 词表命中
        if hit is None and toks[i] in single:
            hit = (i, i + 1, toks[i], "filler")
        if hit is not None:
            a, b, txt, typ = hit
            marks[(a, b)] = {
                "start": float(words[a].get("start", 0.0)),
                "end": float(words[b - 1].get("end", words[b - 1].get("start", 0.0))),
                "word": txt,
                "type": typ,
            }
            i = b
            continue
        i += 1

    # 连续重复口吃：只标前一个
    for i in range(n - 1):
        if not toks[i] or toks[i] != toks[i + 1]:
            continue
        if _overlaps(i, i + 1):
            continue
        marks[(i, i + 1)] = {
            "start": float(words[i].get("start", 0.0)),
            "end": float(words[i].get("end", words[i].get("start", 0.0))),
            "word": toks[i],
            "type": "repeat",
        }

    out = sorted(marks.values(), key=lambda r: r["start"])
    return out


def detect_fillers_semantic(
    transcript: dict,
    *,
    provider: str = "auto",
    filler_words: list[str] | None = None,
) -> list[dict]:
    """语义道：把逐字稿交给本地 Ollama（llm.chat, auto=Ollama 优先）标 filler / 口误。

    模型返回 JSON：{"fillers": [{"start","end","word","reason"}, ...]}。
    只标 filler / 口误，不得删减非 filler 内容。失败时返回 []（规则道兜底）。

    返回与 detect_fillers 同构的列表：[{start, end, word, type:"filler", reason}]。
    """
    from . import llm  # 延迟导入，纯函数路径不强制依赖模型

    segs = transcript.get("segments") or []
    if not segs:
        return []
    lines = []
    for s in segs:
        st = s.get("start", s.get("起", 0))
        en = s.get("end", s.get("止", 0))
        txt = (s.get("text") or s.get("句") or "").strip()
        if txt:
            lines.append(f"[{st:.2f}-{en:.2f}] {txt}")
    if not lines:
        return []

    vocab = "、".join(filler_words) if filler_words else "嗯/啊/呃/哦/那个/就是/然后/其实/怎么说/你知道吗/对吧/反正 等口癖，以及明显的口误重说、半句卡壳"
    prompt = (
        "你是中文口播剪辑助手。下面是一段口播逐字稿（带句段时间码）。"
        "请只把其中【口癖 filler】和【明显口误/重说】的部分挑出来，"
        "不要动正常表达。\n"
        f"参考口癖词表：{vocab}\n"
        "严格只输出 JSON，格式：{\"fillers\":[{\"start\":数字秒,\"end\":数字秒,\"word\":\"原文片段\",\"reason\":\"一句理由\"}]}\n"
        "要求：start/end 必须来自下方时间码且落在该片段内；"
        "没有可删的就返回空数组。不要输出任何解释、不要 markdown 围栏。\n\n"
        + "\n".join(lines)
    )
    try:
        data = llm.json_chat(
            [{"role": "user", "content": prompt}],
            provider=provider, json_mode=True, temperature=0.1, timeout=180,
        )
    except Exception as exc:  # noqa: BLE001 - 语义道失败不阻塞规则道
        return [{"start": 0.0, "end": 0.0, "word": "",
                 "type": "filler", "reason": f"语义道不可用: {type(exc).__name__}"}] if False else []

    out: list[dict] = []
    for item in (data.get("fillers") or []):
        try:
            st = float(item.get("start", 0))
            en = float(item.get("end", 0))
        except (TypeError, ValueError):
            continue
        if en <= st:
            continue
        out.append({
            "start": round(st, 3), "end": round(en, 3),
            "word": str(item.get("word", "")), "type": "filler",
            "reason": str(item.get("reason", "语义道")),
        })
    return out


def union_fillers(*lists: list[dict]) -> list[dict]:
    """多道 filler 结果取并集，按 start 排序、去重（同 start/end/word 视为重复）。"""
    seen: set[tuple] = set()
    out: list[dict] = []
    for lst in lists:
        for r in lst or []:
            key = (round(float(r.get("start", 0)), 3), round(float(r.get("end", 0)), 3),
                   r.get("word", ""), r.get("type", "filler"))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    out.sort(key=lambda r: float(r.get("start", 0)))
    return out
