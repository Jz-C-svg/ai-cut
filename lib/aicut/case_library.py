r"""case_library · 剪辑案例库：历史决策单 + 成片数据 + 评分 → few-shot 检索。

规划前用 search_similar(goal) 检索 top-k 相似案例注入提示词，
让大脑"参考自己最像的成功案例"而不是凭风格卡空想。

案例来源：
- scripts/import_cases.py：从 08-data/评估-*.md、06-drafts/*.sheet.json、07-output 批量导入
- scripts/retrospect.py：每次成片复盘自动写入
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import memory_semantic as ms

ROOT = ms.ROOT


def add_case(name: str, sheet: dict | None, score: float | None = None,
             post_data: dict | None = None, lessons: str = "") -> str:
    """写入一条案例。name 为工程名；score 为发布前六维分；lessons 为复盘要点。"""
    clips = (sheet or {}).get("clips") or []
    dur = sum(float(c.get("end", 0)) - float(c.get("start", 0)) for c in clips)
    body_parts = [
        f"案例:{name}",
        f"风格:{(sheet or {}).get('style') or (sheet or {}).get('template') or '未知'}",
        f"时长:{dur:.1f}s",
        f"片段数:{len(clips)}",
        f"封面:{(sheet or {}).get('cover_text') or ''}",
        f"CTA:{(sheet or {}).get('cta') or ''}",
    ]
    if score is not None:
        body_parts.append(f"评分:{score}")
    if post_data:
        body_parts.append("数据:" + json.dumps(post_data, ensure_ascii=False)[:500])
    if lessons:
        body_parts.append(f"复盘:{lessons}")
    key = f"case:{name}"
    ms.upsert(key, "；".join(body_parts), kind="case", meta={
        "name": name, "score": score, "duration": round(dur, 2),
        "n_clips": len(clips), "lessons": lessons,
        "ts": time.time(),
    })
    return key


def search_similar(goal: str, top_k: int = 3) -> list[dict]:
    """按目标语义检索相似案例（供 agent 规划前注入 few-shot）。"""
    return ms.search(goal, kind="case", top_k=top_k)


def brief_for_planner(goal: str, top_k: int = 3) -> list[str]:
    """生成可拼进规划提示词的案例摘要行。"""
    lines = []
    for hit in search_similar(goal, top_k=top_k):
        meta = hit.get("meta") or {}
        if hit["score"] <= 0:
            continue
        lines.append(
            f"[相似案例 {hit['score']:.2f}] {meta.get('name')}："
            f"风格={meta.get('风格') or '见原文'} 时长={meta.get('duration')}s "
            f"片段={meta.get('n_clips')} 评分={meta.get('score') or '无'}"
            + (f" 复盘要点={meta.get('lessons')}" if meta.get("lessons") else "")
        )
    return lines


def import_from_workspace() -> int:
    """从项目工作区批量导入：03-scripts/*.sheet.json + 08-data/评估-*.md 提取分数。"""
    added = 0
    # 分数索引：评估-<工程名>.md 里抓 "XX/100"
    scores: dict[str, float] = {}
    data_dir = ROOT / "08-data"
    if data_dir.exists():
        import re
        for md in data_dir.glob("评估-*.md"):
            m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*100", md.read_text(encoding="utf-8", errors="ignore"))
            if m:
                scores[md.stem.replace("评估-", "")] = float(m.group(1))
    scripts_dir = ROOT / "03-scripts"
    if scripts_dir.exists():
        for f in sorted(scripts_dir.glob("*.sheet.json")):
            try:
                sheet = json.loads(f.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                continue
            name = sheet.get("project") or f.stem.replace(".sheet", "")
            add_case(name, sheet, score=scores.get(name))
            added += 1
    return added
