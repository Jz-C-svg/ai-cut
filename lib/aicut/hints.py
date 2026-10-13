r"""主体循环用到的几段"话"集中放这里，避免散落在 agent.py 里。

  SEMANTIC_QUESTIONS  哪些技能做完之后值得"看一眼画面"（本地视觉模型回答 YES/NO）
  dry_note()          演算时给 shell 类技能的统一说明
"""
from __future__ import annotations

# 只有"从素材出片"这类会真正产出画面的步骤，才值得花几秒让视觉模型看一眼
SEMANTIC_QUESTIONS: dict[str, str] = {
    "generate_draft": "这一帧里有清晰的人物或可辨认的主体吗",
}


def question_for(skill_id: str) -> str:
    return SEMANTIC_QUESTIONS.get(skill_id, "")


def dry_note(skill_id: str, args: dict) -> str:
    """演算时 shell 技能不会真的执行——必须说清楚，避免"以为出了片"。"""
    return f"演算：跳过 shell 技能 {skill_id}（真跑会执行外部脚本，参数 {args}）"
