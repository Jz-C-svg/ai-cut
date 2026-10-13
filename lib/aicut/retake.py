"""重说口误检测 —— 找出「说错 → 停顿 → 重说」里作废的前一遍。

移植自 external/refs/ai-xunjian/firstcut/retake.mjs（约 80 行纯算法）。
判定原则（保守优先，宁漏勿误剪）：
  ① 只比较时间上紧挨着的句子（间隔 ≤ gap 秒、往后最多看 lookahead 句）——
     隔很远的相似句多半是刻意呼应，不动；
  ② 两种命中：整句高相似（说错个别词重说）/ 前句是后句的开头（半句卡壳重来）；
  ③ 命中永远剪「前一遍」保「后一遍」——重说的那遍才是想要的。

纯函数、零第三方依赖（Levenshtein 为纯 Python 实现，不引 python-Levenshtein）。
"""
from __future__ import annotations

import re

# 归一化：去标点 / 空白 / 统一小写（对齐 mjs 的 norm）
_PUNCT_RE = re.compile(r"[\s，。、？！；：“”‘’…—·,.?!;:'\"()（）《》<>【】\[\]]")


def norm(text: object) -> str:
    """文本归一化：去标点、空白，转小写。"""
    return _PUNCT_RE.sub("", str(text or "")).lower()


def levenshtein(a: str, b: str) -> int:
    """标准 Levenshtein 编辑距离（DP，滚动数组）。纯 Python，零依赖。"""
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        ca = a[i - 1]
        for j in range(1, n + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[n]


def sim_ratio(a: str, b: str) -> float:
    """相似度 = 1 - lev / max(len_a, len_b)。完全相同为 1.0。"""
    L = max(len(a), len(b))
    return 0.0 if L == 0 else 1.0 - levenshtein(a, b) / L


def detect_retakes(
    segments: list[dict],
    *,
    sim: float = 0.8,
    gap: float = 8.0,
    min_chars: int = 5,
    lookahead: int = 3,
    edge: float = 0.05,
    min_cut: float = 0.3,
) -> list[dict]:
    """输入逐字稿句段 [{start, end, text}]，输出重说（前一遍）区间列表。

    返回：[{keep_start, keep_end, remove_start, remove_end,
            removed_text, kept_text, reason, similarity}]
      - remove_*  待剪（说错的前一遍）的时间区间，边缘各扩 edge 秒；
      - keep_*    保留（说对的后一遍）的时间区间；
      - reason    "整句相似" / "前缀相似"；
      - similarity 命中时的相似度（整句或前缀，取实际命中的那个）。

    规则：
      - 向后最多看 lookahead 句；两句间隔（后句起 - 前句止）≤ gap 才比；
      - 整句相似度 ≥ sim 命中；或前句是后句前缀（后句不短于前句）且前缀相似度
        ≥ max(sim, 0.85) 命中；
      - 归一化后短于 min_chars 的句子不参与比较（太短没有可比性）；
      - 命中永远删前一遍保后一遍；
      - 剪前把区间向两侧各扩 edge 秒呼吸；扩后不足 min_cut 秒的不动刀。
    """
    segs = [s for s in (segments or [])
            if s and s.get("end") is not None and s.get("start") is not None
            and float(s["end"]) > float(s["start"])]
    removed_idx: set[int] = set()
    out: list[dict] = []

    for i, si in enumerate(segs):
        if i in removed_idx:
            continue
        ni = norm(si.get("text", ""))
        if len(ni) < min_chars:
            continue
        s_i, e_i = float(si["start"]), float(si["end"])
        for j in range(i + 1, min(i + 1 + lookahead, len(segs))):
            if j in removed_idx:
                continue
            sj = segs[j]
            if float(sj["start"]) - e_i > gap:
                break  # 隔太远，往后更隔，直接停
            nj = norm(sj.get("text", ""))
            if len(nj) < min_chars:
                continue
            # Changed quantities, negation and prerequisites are distinct information.
            protected=r'\d+(?:\.\d+)?|[一二三四五六七八九十百千万]+(?:克|件|条|毫升|度|码|个)|不|别|只有|必须|如果'
            if re.findall(protected,ni)!=re.findall(protected,nj):
                continue
            s_j, e_j = float(sj["start"]), float(sj["end"])
            # ① 整句高相似（重说时说对了个别词）
            full = sim_ratio(ni, nj)
            # ② 半句卡壳重来：前句 ≈ 后句的开头（后句至少不短于前句才算"接着说完"）
            prefix = sim_ratio(ni, nj[:len(ni)]) if len(nj) >= len(ni) else 0.0
            if full >= sim or prefix >= max(sim, 0.85):
                removed_idx.add(i)
                # 边缘各扩 edge 秒呼吸
                cut_s = max(0.0, s_i - edge)
                cut_e = e_i + edge
                reason = "整句相似" if full >= sim else "前缀相似"
                similarity = round(full if full >= sim else prefix, 4)
                out.append({
                    "keep_start": round(s_j, 3),
                    "keep_end": round(e_j, 3),
                    "remove_start": round(cut_s, 3),
                    "remove_end": round(cut_e, 3),
                    "removed_text": str(si.get("text", "")).strip(),
                    "kept_text": str(sj.get("text", "")).strip(),
                    "reason": reason,
                    "similarity": similarity,
                })
                break  # i 已判废，不再往后配对

    # 太碎的（< min_cut 秒）不值得动刀
    out = [r for r in out if r["remove_end"] - r["remove_start"] >= min_cut]
    out.sort(key=lambda r: r["remove_start"])
    return out


def merge_spans(spans: list[tuple[float, float]], gap: float = 0.1) -> list[tuple[float, float]]:
    """合并重叠 / 相邻（间隔 ≤ gap）的区间。返回升序。"""
    ordered = sorted((float(a), float(b)) for a, b in spans if b > a)
    merged: list[tuple[float, float]] = []
    for s, e in ordered:
        if merged and s <= merged[-1][1] + gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


# ---------------------------------------------------------------------------
# v3.2 A3：SmartCut 重复短语检测（词/字级滑动窗口，与上面的句级 Levenshtein 互补）
# ---------------------------------------------------------------------------
# 参考：external/refs 中未找到 qkirara/Smart-Cut 的精确源码（drhema-mcp-cut 里的
# smartcut 是句级 jaccard/seq 相似度，与现有 detect_retakes 同源）。此处按任务
# 语义实现「连续 ≥ min_phrase_len 个 token 完全相同」的短语级重复检测：
#   - 逐字稿无分词器时，把归一化后的每个汉字当作一个 token（3 字 ≈ 3 词）；
#   - 滑动窗口找最长重复短语，两次出现时间间隔 ≤ max_gap_sec 才判为口误重复
#     （间隔太长多半是有意呼应，不动）；
#   - 命中永远删「前几次」保「末次」——重说的那遍才是想要的。


def _tokenize_segments(segments: list[dict]) -> list[tuple[str, float, float]]:
    """把句段逐字稿拍平成 token 流：[(归一化字符, start, end)]。

    无分词器时按字切分；句段时长在其字符间均分（近似，够定位用）。
    """
    toks: list[tuple[str, float, float]] = []
    for s in segments or []:
        if not s:
            continue
        try:
            st, en = float(s.get("start") or 0.0), float(s.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        if en <= st:
            continue
        text = norm(s.get("text", ""))
        if not text:
            continue
        dur = en - st
        n = len(text)
        for k, ch in enumerate(text):
            toks.append((ch, st + k * dur / n, st + (k + 1) * dur / n))
    return toks


def detect_repeated_phrases(
    segments: list[dict],
    *,
    min_phrase_len: int = 3,
    max_gap_sec: float = 5.0,
) -> list[dict]:
    """词/字级重复短语检测（SmartCut 思路）——detect_retakes 的第三道补充。

    输入逐字稿句段 [{start, end, text}]（与 detect_retakes 同构）。
    输出：[{remove_start, remove_end, keep_start, keep_end,
            phrase, reason, occurrence_count}]
      - remove_*  待剪（重复的前几次出现）的时间区间；
      - keep_*    保留（最后一次出现）的时间区间；
      - phrase    重复的短语（归一化后）；
      - reason    固定 "重复短语"；
      - occurrence_count  该短语在窗口内出现的次数。

    规则：
      - 把逐字稿按字拍平成 token 流（无分词器时一字一 token）；
      - 对每个起点 i，往后找 j（不与 i 重叠）的最长公共前缀 L，L ≥ min_phrase_len；
      - 两次出现间隔（后一次起 - 前一次止）≤ max_gap_sec 才判定为口误重复；
      - 命中删前一次、保后一次；occurrence_count 统计该短语总出现次数。

    与 detect_retakes 的关系：互补取并集——本函数抓「短语级连续重复」
    （如"我们今天我们今天去公园"），detect_retakes 抓「句级高相似/前缀卡壳」。
    """
    toks = _tokenize_segments(segments)
    n = len(toks)
    if n < min_phrase_len * 2:
        return []

    out: list[dict] = []
    removed_span: list[tuple[float, float]] = []  # 已删区间，避免重复标

    def _already_removed(a: float, b: float) -> bool:
        return any(not (b <= x or a >= y) for x, y in removed_span)

    i = 0
    while i < n:
        best_j = -1
        best_len = 0
        # j 必须与 i 的窗口不重叠：j >= i + min_phrase_len
        for j in range(i + min_phrase_len, n):
            # 求 toks[i:] 与 toks[j:] 的最长公共前缀
            L = 0
            while i + L < n and j + L < n and toks[i + L][0] == toks[j + L][0]:
                L += 1
            if L >= min_phrase_len and L > best_len:
                # 两次出现的间隔 = 第二次起 - 第一次止
                gap = toks[j][1] - toks[i + L - 1][2]
                if gap <= max_gap_sec + 1e-6:
                    best_j, best_len = j, L
        if best_j > 0:
            r_s, r_e = toks[i][1], toks[i + best_len - 1][2]
            k_s, k_e = toks[best_j][1], toks[best_j + best_len - 1][2]
            phrase = "".join(toks[i + k][0] for k in range(best_len))
            if not _already_removed(r_s, r_e):
                # 统计该短语总出现次数
                occ = 0
                p = 0
                pl = best_len
                while p <= n - pl:
                    if "".join(toks[p + k][0] for k in range(pl)) == phrase:
                        occ += 1
                        p += pl
                    else:
                        p += 1
                out.append({
                    "remove_start": round(r_s, 3),
                    "remove_end": round(r_e, 3),
                    "keep_start": round(k_s, 3),
                    "keep_end": round(k_e, 3),
                    "phrase": phrase,
                    "reason": "重复短语",
                    "occurrence_count": occ,
                })
                removed_span.append((r_s, r_e))
            i += best_len
            continue
        i += 1

    out.sort(key=lambda r: r["remove_start"])
    return out


# ---------------------------------------------------------------------------
# v3.2 A2：口误分类（参考 voice-over-skills 的四类：重复/卡顿/语气词/长静音）
# ---------------------------------------------------------------------------
# 外部未找到 voice-over-skills 精确源码，按四类语义实现一个分类调度器：
# 输入任一检测道返回的 hit dict，根据其 reason/type/kind 字段归到四类之一。

_DISFLUENCY_CATEGORIES = ("重复", "卡顿", "语气词", "长静音")


def classify_disfluency(hit: dict) -> str:
    """把一个检测出的口误区间分类为「重复 / 卡顿 / 语气词 / 长静音」之一。

    入参 hit 来自任一检测道（retake / repeated_phrases / filler / silence）：
      - 看 reason（中文）、type（小写英文）、kind 字段综合判断；
      - 长静音：能量道（无文本）；
      - 语气词：filler 词表命中（嗯/啊/那个…）；
      - 重复  ：短语连续重复 / 整句重说 / 连续重复词；
      - 卡顿  ：半句卡壳重来（前缀相似），或兜底归此类。
    """
    if not isinstance(hit, dict):
        return "卡顿"
    reason = str(hit.get("reason") or "")
    typ = str(hit.get("type") or "").lower()
    kind = str(hit.get("kind") or "").lower()
    word = str(hit.get("word") or "")

    # 长静音：能量道
    if kind == "silence" or typ == "silence" or "静音" in reason or "silence" in typ:
        return "长静音"
    # 语气词：filler 词表
    if typ == "filler" or "语气词" in reason or "filler" in reason.lower():
        return "语气词"
    # 重复：短语重复 / 整句重说 / 连续重复词
    if (typ == "repeat" or "重复" in reason or "整句相似" in reason
            or "重复短语" in reason):
        return "重复"
    # 卡顿：半句卡壳重来
    if "前缀" in reason or "卡壳" in reason:
        return "卡顿"
    # 兜底：有词但短且像语气词
    from . import filler as _fl  # 延迟，避免循环
    if word and _fl._norm_token(word) in {_fl._norm_token(x) for x in _fl.DEFAULT_FILLERS}:
        return "语气词"
    return "卡顿"


def annotate_disfluencies(hits: list[dict]) -> list[dict]:
    """给一组检测结果补一个 disfluency 字段（原地加，返回同一列表）。"""
    for h in hits or []:
        if isinstance(h, dict):
            h.setdefault("disfluency", classify_disfluency(h))
    return hits
