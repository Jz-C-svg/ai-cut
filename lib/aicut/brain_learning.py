"""主脑联网学习：检索、提炼、来源留存、语义索引与规划注入。"""
from __future__ import annotations

import time
from dataclasses import dataclass

from . import learn
from . import memory_semantic
from .memory import Memory, open_memory
from . import web_research


@dataclass
class LearningResult:
    topic: str
    points: int
    recipes: int
    sources: list[str]
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "topic": self.topic,
            "points": self.points,
            "recipes": self.recipes,
            "sources": self.sources,
            "error": self.error,
        }


class BrainLearner:
    """主脑的联网学习入口。

    联网只负责获取公开资料；执行权限仍由技能注册表控制。
    """

    def __init__(self, memory: Memory | None = None, provider: str = "auto") -> None:
        self.memory = memory or open_memory()
        self.provider = provider

    def learn(self, topic: str, *, question: str | None = "", want_recipes: bool = True,
              tokens: int = 3000) -> LearningResult:
        topic = topic.strip()
        if not topic:
            raise ValueError("topic 不能为空")
        prompt = (question or "").strip() or (
            f"检索「{topic}」的公开可靠资料，提炼 5-8 条可直接用于 AI 剪辑主体的做法；"
            "每条说明适用条件、风险和来源链接。不要复制原文表达。")
        started = time.time()
        primary_error = ""
        try:
            result = learn.web_learn_custom(
                topic, prompt, tokens=tokens, provider=self.provider,
                mem=self.memory, want_recipes=want_recipes)
        except Exception as exc:
            primary_error = f"{type(exc).__name__}: {exc}"[:240]
            result = self._fallback_search(topic, prompt)
        fact = self.memory.fact(f"web:{topic}") or {}
        points = fact.get("要点") or []
        sources = [str(x) for x in (fact.get("来源") or []) if x]
        text = "\n".join(
            str(p.get("要点") if isinstance(p, dict) else p)
            for p in points)
        memory_semantic.upsert(
            f"web:{topic}", f"主题：{topic}\n{text}",
            kind="web", meta={"sources": sources[:24], "learned_at": time.time()})
        error = str(result.get("error") or "")
        if primary_error:
            suffix = "备用公开搜索已接管" if points else "备用通道未产出要点"
            error = f"主通道失败：{primary_error}；{suffix}"
        self.memory.add_episode(
            task="brain-learning",
            kind="online_learning",
            goal=topic,
            detail={"topic": topic, "points": len(points), "recipes": result.get("配方数", 0),
                    "sources": sources[:12], "seconds": round(time.time() - started, 2),
                    "error": error},
            evidence=";".join(sources[:6]),
            ok=bool(points),
        )
        return LearningResult(topic, len(points), int(result.get("配方数", 0)),
                              sources[:24], error)

    def _fallback_search(self, topic: str, question: str) -> dict:
        """主通道（联网模型）失败时走公开搜索，不伪装成模型提炼成功。"""
        found = web_research.search_all(topic, lang="both", max_results=10)
        snippets = found.get("snippets") or []
        points = [{"要点": s.get("text", "")[:300], "来源": s.get("url", "")}
                  for s in snippets if s.get("text")]
        sources = [s.get("url") for s in snippets if s.get("url")]
        self.memory.set_fact(
            f"web:{topic}",
            {"查询": question, "来源": sources[:24], "要点": points[:24],
             "原文": "\n\n".join(s.get("text", "")[:500] for s in snippets[:8]),
             "降级": "公开搜索摘要，未经过 LLM 提炼"},
            source="web-fallback",
        )
        return {"主题": topic, "要点数": len(points), "配方数": 0,
                "来源数": len(sources), "来源": sources}

    def learn_many(self, topics: list[str], *, want_recipes: bool = True) -> list[dict]:
        results = []
        for topic in topics:
            results.append(self.learn(topic, want_recipes=want_recipes).as_dict())
        return results
