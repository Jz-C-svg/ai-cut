# -*- coding: utf-8 -*-
r"""架构 RAG 轻量检索增强（arch_rag）——纯 Python，零重依赖。

定位：
  把 content-library\architectures\ 下全部 19 个架构档案 + film-language.json
  拆成可检索的「段落块」，检索时用 TF-IDF 关键词匹配 + 关键词共现相似度召回
  最相关的架构段落规则，直接拼进 LLM prompt。

刻意不引入 sentence-transformers / 向量库：
  本项目知识总量小（几十块、几万字），纯 Python 二元语法 + 词频逆文档频率
  足够支撑「段落级」召回，启动零成本、离线可跑。

接口：
    rag = ArchRAG()
    rag.load_index()
    hits = rag.retrieve("在家坐月子 vs 月子中心 三问测一测", content_layer="shortform", top_k=3)
    ctx  = rag.get_rag_context("钩子怎么设计", content_layer="shortform")
    seg  = rag.retrieve_segment("wenda-xuannian", "钩子")
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .paths import CONTENT_LIBRARY

ARCH_DIR = CONTENT_LIBRARY / "architectures"

# 停用词（检索时忽略，避免无区分度）
_STOP = set("的 了 是 在 我 你 他 她 它 们 这 那 和 与 及 或 一个 没有 我们 你们 "
            "什么 怎么 这个 那个 一下 就是 还是 但是 因为 所以 如果 虽然 而且".split())

# 额外领域关键词表（提升共现检索的命中率，来自架构匹配关键词沉淀）
_DOMAIN_KEYWORDS = [
    "月子", "月子中心", "坐月子", "在家坐月子", "产后", "宝妈", "产妇", "新生儿", "宝宝",
    "月嫂", "护士", "产康", "月子餐", "护理", "母婴", "产褥", "母乳", "黄疸",
    "钩子", "CTA", "开头", "结尾", "反转", "铺垫", "干货", "测评", "对比", "教程",
    "探店", "卡点", "混剪", "vlog", "日常", "故事", "情绪", "悬念", "提问", "测一测",
    "三问", "合集", "盘点", "挑战", "趋势", "反应", "评论", "段子", "剧情",
    "蒙太奇", "景别", "特写", "近景", "中景", "全景", "转场", "闪白", "叠化", "硬切",
    "剪辑点", "动作剪辑点", "情绪剪辑点", "音乐剪辑点", "节奏", "快切", "长镜头",
]


# ───────────────────── 中文轻量分词 ─────────────────────

def tokenize(text: str) -> list[str]:
    """轻量分词：中文按 2-gram 切，英文/数字按词切，剔除停用词和单字。

    纯中文场景下 2-gram 的召回率足够；领域关键词优先整词保留以提升共现权重。
    """
    if not text:
        return []
    text = str(text).lower()
    tokens: list[str] = []
    # 先挖领域整词
    for kw in _DOMAIN_KEYWORDS:
        if kw and kw in text:
            tokens.append(kw.lower())
    # 英文/数字词
    for w in re.findall(r"[a-z0-9][a-z0-9\-]{1,}", text):
        if w not in _STOP and len(w) > 1:
            tokens.append(w)
    # 中文 2-gram
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk:
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in _STOP:
                tokens.append(bg)
        if len(run) == 1 and run not in _STOP:
            tokens.append(run)
    return tokens


# ───────────────────── 段落块切分 ─────────────────────

def _blocks_from_arch(arch: dict) -> list[dict]:
    """把单个架构档案拆成可检索段落块，每块带元数据。"""
    arch_id = arch.get("id", "?")
    layer = arch.get("content_layer", "shortform")
    name = arch.get("名称", arch_id)
    blocks: list[dict] = []

    # 1) 档案总览块
    overview = "；".join(str(x) for x in (
        [arch.get("描述", "")] + (arch.get("别名") or [])[:4]
    ) if x)
    feat = arch.get("适用素材特征") or {}
    if feat:
        overview += "。适用：" + "、".join(
            str(x) for x in (feat.get("内容类型") or []) + (feat.get("主角类型") or [])[:4])
    blocks.append({
        "arch_id": arch_id, "arch_name": name, "content_layer": layer,
        "field": "档案总览", "text": overview,
    })

    # 2) 段落序列：每段一块
    for seg in arch.get("段落序列") or []:
        body = "。".join(str(x) for x in (
            f"段落功能={seg.get('功能', '')}",
            f"时长占比{seg.get('时长占比', '')}",
            f"信息密度{seg.get('信息密度', '')}",
            f"节奏{seg.get('节奏', '')}",
            f"情绪{seg.get('情绪', '')}",
            str(seg.get("描述", "")),
            f"剪辑要点：{seg.get('剪辑要点', '')}",
        ) if x)
        blocks.append({
            "arch_id": arch_id, "arch_name": name, "content_layer": layer,
            "field": f"段落-{seg.get('功能', '')}", "text": body,
        })

    # 3) 钩子规则块
    hk = arch.get("钩子规则") or {}
    if hk:
        body = "。".join(str(x) for x in (
            f"类型={'/'.join(map(str, hk.get('类型', [])[:4]))}",
            f"策略：{hk.get('策略', '')}",
            f"话术：{'/'.join(map(str, hk.get('话术模式', [])[:4]))}",
            f"视觉配合：{hk.get('视觉配合', '')}",
        ) if x)
        blocks.append({
            "arch_id": arch_id, "arch_name": name, "content_layer": layer,
            "field": "钩子规则", "text": body,
        })

    # 4) CTA 规则块
    ct = arch.get("CTA规则") or {}
    if ct:
        body = "。".join(str(x) for x in (
            f"类型={'/'.join(map(str, ct.get('类型', [])[:4]))}",
            f"策略：{ct.get('策略', '')}",
            f"话术：{'/'.join(map(str, ct.get('话术模式', [])[:4]))}",
        ) if x)
        blocks.append({
            "arch_id": arch_id, "arch_name": name, "content_layer": layer,
            "field": "CTA规则", "text": body,
        })

    # 5) 信息密度 + 常见失误块
    mistakes = "、".join(arch.get("常见失误") or [])
    body = f"信息密度目标：{arch.get('信息密度目标', '')}。常见失误：{mistakes}"
    blocks.append({
        "arch_id": arch_id, "arch_name": name, "content_layer": layer,
        "field": "密度与失误", "text": body,
    })
    return blocks


def _blocks_from_film_language(fl: dict) -> list[dict]:
    """电影语言档案按大类切块。"""
    blocks: list[dict] = []
    for cat in ("剪辑理论", "镜头语言", "叙事结构", "节奏语言"):
        node = fl.get(cat)
        if not node:
            continue
        # 把该大类下每个子类压成一段
        for sub, detail in (node.items() if isinstance(node, dict) else []):
            if isinstance(detail, dict):
                bits = []
                for k in ("定义", "适用场景", "决策规则", "叙事功能", "心理暗示", "参数化建议"):
                    if detail.get(k):
                        bits.append(f"{k}：{detail[k]}")
                body = f"{sub}——" + "；".join(str(b)[:200] for b in bits)
            else:
                body = f"{sub}——{detail}"
            blocks.append({
                "arch_id": "film-language", "arch_name": "电影剪辑语言",
                "content_layer": "film-level",
                "field": f"{cat}-{sub}", "text": body[:800],
            })
    return blocks


# ───────────────────── 索引与检索 ─────────────────────

class ArchRAG:
    """架构档案轻量检索器（TF-IDF + 关键词共现）。"""

    def __init__(self) -> None:
        self._blocks: list[dict] = []
        self._tf: list[Counter] = []       # 每块的词频
        self._bv: list[Counter] = []       # 每块的 idf 加权向量
        self._df: Counter = Counter()      # 文档频率
        self._idf: dict[str, float] = {}
        self._loaded = False

    # ── 建索引 ──
    def load_index(self) -> "ArchRAG":
        """扫描 architectures/*.json 建立检索索引。幂等。"""
        if self._loaded:
            return self
        blocks: list[dict] = []
        # 19 类架构
        for fp in sorted(ARCH_DIR.glob("*.json")):
            if fp.name in ("architectures.json", "film-language.json", "_schema_template.json"):
                continue
            try:
                arch = json.loads(fp.read_text(encoding="utf-8-sig"))
                blocks.extend(_blocks_from_arch(arch))
            except Exception:  # noqa: BLE001
                continue
        # 电影语言
        fl_fp = ARCH_DIR / "film-language.json"
        if fl_fp.exists():
            try:
                blocks.extend(_blocks_from_film_language(json.loads(fl_fp.read_text(encoding="utf-8-sig"))))
            except Exception:  # noqa: BLE001
                pass
        # 历史失败案例（brain_cases/，进化闭环：犯错→补库→下次召回）
        try:
            from . import brain_logger
            blocks.extend(brain_logger.load_case_blocks())
        except Exception:  # noqa: BLE001
            pass

        self._blocks = blocks
        # TF-IDF 统计
        for b in blocks:
            b["_tokens"] = tokenize(b["text"])
            self._tf.append(Counter(b["_tokens"]))
            for term in set(b["_tokens"]):
                self._df[term] += 1
        n_docs = max(1, len(blocks))
        self._idf = {t: math.log((1 + n_docs) / (1 + df)) + 1.0
                     for t, df in self._df.items()}
        self._bv = [self._vec(tf) for tf in self._tf]
        self._loaded = True
        return self

    def _vec(self, tokens: list[str]) -> Counter:
        c = Counter(tokens)
        # 权重 = 词频 × idf
        return Counter({t: f * self._idf.get(t, 1.0) for t, f in c.items()})

    @staticmethod
    def _cosine(a: Counter, b: Counter) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[k] * b[k] for k in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    # ── 检索 ──
    def retrieve(self, query: str, content_layer: str | None = None,
                 top_k: int = 3, arch_id: str | None = None) -> list[dict]:
        """召回最相关的架构段落块。

        content_layer: 限定内容层级（shortform 块只在 shortform 查询时召回；
                       longform/film-level 查询放开 film-language 块）。
        arch_id: 限定只在某个架构档案内召回（用于 retrieve_segment）。
        返回 [{score, arch_id, arch_name, content_layer, field, text}]。
        """
        self.load_index()
        qv = self._vec(tokenize(query))
        scored: list[dict] = []
        for i, b in enumerate(self._blocks):
            if arch_id and b["arch_id"] != arch_id:
                continue
            is_case = b.get("content_layer") == "case" or b.get("arch_id") == "brain-case"
            if not is_case:
                if content_layer == "shortform" and b["content_layer"] not in ("shortform",):
                    continue
                if content_layer in ("longform", "film-level") and b["arch_id"] != "film-language" \
                        and b["content_layer"] not in ("longform", "film-level"):
                    continue
            score = self._cosine(qv, self._bv[i])
            # 共现加分：领域关键词整词命中直接 +0.05
            hit_kw = [kw for kw in _DOMAIN_KEYWORDS
                      if kw in query and kw in b["text"]]
            score += 0.05 * len(hit_kw)
            # 历史案例优先：案例关键词命中再 +0.3，让「踩过的坑」排到最前
            if is_case:
                case_kws = b.get("keywords") or []
                score += 0.15 + 0.1 * sum(1 for kw in case_kws if kw and kw in query)
            if score > 0:
                scored.append({
                    "score": round(score, 4),
                    "arch_id": b["arch_id"], "arch_name": b["arch_name"],
                    "content_layer": b["content_layer"], "field": b["field"],
                    "text": b["text"],
                })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def retrieve_segment(self, arch_id: str, segment_keyword: str) -> list[dict]:
        """在指定架构内检索具体段落规则（如「钩子」「CTA」「分点干货」）。"""
        return self.retrieve(segment_keyword, arch_id=arch_id, top_k=4)

    def get_rag_context(self, query: str, content_layer: str | None = None,
                        top_k: int = 3) -> str:
        """返回格式化的 RAG 上下文文本，可直接拼进 LLM prompt。"""
        hits = self.retrieve(query, content_layer=content_layer, top_k=top_k)
        if not hits:
            return ""
        lines = ["【RAG 召回的相关架构段落规则】"]
        for h in hits:
            lines.append(
                f"- [{h['arch_name']} · {h['field']}] {h['text'][:260]}")
        return "\n".join(lines)


# 模块级单例，避免每次调用重建索引
_SINGLETON: ArchRAG | None = None


def get_rag() -> ArchRAG:
    """获取全局共享的 ArchRAG 单例（懒加载）。"""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = ArchRAG().load_index()
    return _SINGLETON


def refresh_rag() -> ArchRAG:
    """重置并重建 RAG 单例（新增案例/档案后调用，让新块立即可被检索）。"""
    global _SINGLETON
    _SINGLETON = None
    return get_rag()


def get_rag_context(query: str, content_layer: str | None = None,
                   top_k: int = 3) -> str:
    """便捷函数：一行拿到 RAG 上下文文本。"""
    return get_rag().get_rag_context(query, content_layer=content_layer, top_k=top_k)


if __name__ == "__main__":
    import sys
    sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    rag = ArchRAG().load_index()
    print(f"[arch_rag] 索引块数: {len(rag._blocks)}")
    q = "在家坐月子和月子中心有什么区别，三问测一测哪个适合你"
    for h in rag.retrieve(q, content_layer="shortform", top_k=3):
        print(f"  {h['score']:.3f} | {h['arch_name']} · {h['field']}")
    print(rag.get_rag_context(q, content_layer="shortform"))
