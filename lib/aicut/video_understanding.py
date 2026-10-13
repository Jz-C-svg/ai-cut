"""统一视频理解档案：把确定性信号、转写、视觉档案和创作判断放在一起。

该模块不把模型猜测伪装成事实。所有输出分为 evidence、inference、to_verify，
供剪辑器和主脑分别使用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _load_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    import json
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _text_map(segments: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    mapped = []
    for i, seg in enumerate(segments):
        start = max(0.0, float(seg.get("start", 0)))
        end = min(duration or float(seg.get("end", start)), float(seg.get("end", start)))
        if end <= start:
            continue
        text = str(seg.get("text", "")).strip()
        mapped.append({
            "i": i, "start": round(start, 3), "end": round(end, 3),
            "text": text, "chars": len(text), "words_per_second": round(len(text) / (end - start), 2),
        })
    return mapped


def build(video: str | Path, *, signal: dict[str, Any] | None = None,
          visual: dict[str, Any] | None = None,
          transcript: dict[str, Any] | None = None,
          semantic: dict[str, Any] | None = None,
          intent: dict[str, Any] | None = None,
          ai_understanding: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a versioned, evidence-first understanding artifact."""
    path = Path(video)
    signal = signal or {}
    visual = visual or {}
    transcript = transcript or {}
    duration = float(signal.get("duration") or visual.get("duration") or 0)
    segments = _text_map(list(transcript.get("segments") or []), duration)
    shots = list(visual.get("shots") or [])
    if not shots:
        shots = [{"i": i, "t0": s.get("start", 0), "t1": s.get("end", duration),
                  "len": s.get("end", duration) - s.get("start", 0)}
                 for i, s in enumerate(signal.get("场景") or [])]

    evidence = {
        "media": {"video": str(path), "duration": round(duration, 3),
                  "has_audio": bool(signal.get("响度") or transcript.get("segments"))},
        "shots": shots,
        "transcript": segments,
        "pauses": signal.get("停顿区间_秒") or [],
        "cuts": signal.get("镜头切点_秒") or visual.get("cuts") or [],
        "visual_summary": visual.get("summary") or {},
    }

    coverage = []
    for shot in shots:
        s0, s1 = float(shot.get("t0", 0)), float(shot.get("t1", duration))
        words = [x for x in segments if _overlap(s0, s1, x["start"], x["end"]) > 0]
        coverage.append({
            "shot": shot.get("i", len(coverage)), "start": round(s0, 3), "end": round(s1, 3),
            "visual_role_candidate": "主体镜头" if shot.get("face_score", 0) > 0 else "待确认",
            "spoken_segments": [x["i"] for x in words],
            "text_visual_match": "待视觉模型确认" if not words else "有口播覆盖",
            "quality": shot.get("grade", "未知"),
        })

    recommendations: list[dict[str, Any]] = []
    if not segments:
        recommendations.append({"priority": "high", "action": "先转写并做词级对齐",
                                "reason": "没有可用时间码，不能可靠判断口播与镜头是否同步"})
    if len(shots) <= 1 and duration > 8:
        recommendations.append({"priority": "medium", "action": "增加语义切点或轻微景别变化",
                                "reason": "全片只有一个视觉镜头，信息推进主要依赖字幕"})
    if signal.get("停顿总时长", 0) > duration * 0.25:
        recommendations.append({"priority": "high", "action": "复核停顿裁剪，不要一律删除",
                                "reason": "停顿占比超过四分之一，可能包含自然呼吸或语义边界"})
    for item in coverage:
        if item["quality"] == "糊":
            recommendations.append({"priority": "high", "action": "缩短或替换该镜头",
                                    "shot": item["shot"], "reason": "确定性画质层判定为糊"})
    if any(x["words_per_second"] > 7 for x in segments):
        recommendations.append({"priority": "medium", "action": "拆分字幕或放慢口播节奏",
                                "reason": "部分句子信息密度偏高，移动端阅读风险较大"})

    inference = {
        "content_type": "口播知识型" if segments else "待分类",
        "likely_goal": "收藏/理解" if segments else "待确认",
        "structure_candidate": ["钩子", "解释", "证据/时间点", "单一收束"],
        "visual_text_alignment": coverage,
        "creative_readiness": "needs_review" if recommendations else "ready_for_planning",
    }
    to_verify = [
        "医学、法律、价格、资质和案例等事实必须人工或权威来源核验",
        "视觉模型描述的主体、动作和情绪不能替代人工复核",
        "平台规格与合规规则以发布时官方要求为准",
    ]
    legacy = {
        "信号识别": signal, "文本理解": (ai_understanding or {}).get("文本理解", {}),
        "视觉理解": (ai_understanding or {}).get("视觉理解", []),
    }
    stat = path.stat() if path.exists() else None
    return {
        "schema_version": "video-understanding-1.0",
        "video": str(path),
        "素材指纹": {"大小": stat.st_size, "修改时间": int(stat.st_mtime)} if stat else {},
        "evidence": evidence,
        "inference": inference,
        "recommendations": recommendations,
        "to_verify": to_verify,
        "source_artifacts": {
            "semantic": bool(semantic), "intent": bool(intent),
            "ai_understanding": bool(ai_understanding),
        },
        "legacy": legacy,
        **legacy,
    }


def load_and_build(video: str | Path, *, analysis_path: Path | None = None,
                   visual_path: Path | None = None, transcript_path: Path | None = None,
                   semantic_path: Path | None = None, intent_path: Path | None = None,
                   ai_path: Path | None = None) -> dict[str, Any]:
    return build(video, signal=_load_json(analysis_path), visual=_load_json(visual_path),
                 transcript=_load_json(transcript_path), semantic=_load_json(semantic_path),
                 intent=_load_json(intent_path), ai_understanding=_load_json(ai_path))
