r"""前置检查：挡住"条件不满足还要动手"的步骤。

主体要像个靠谱的剪辑师：没有草稿就别去改时间线，剪映没开就别去点界面，
没有识别结果就别去删口误。宁可明确报一句"缺什么"，也不要动手之后留下半成品。
"""
from __future__ import annotations


def precheck(skill, agent) -> str:
    """返回空字符串表示可以执行；否则返回给用户看的原因。"""
    if skill is None:
        return "未知技能"
    if skill.channel == "draft" and getattr(agent, "draft", None) is None:
        return ("这一步需要草稿（工程通道）。当前只有素材，先用 prepare_material + "
                "generate_draft 出片，或者用 --draft 指定要剪的草稿")
    if skill.channel == "ui":
        from . import ui

        state = ui.info()
        if not state.get("running"):
            return "剪映没开：界面通道技能（花字/云字体/导出/打开草稿）需要先启动剪映专业版"
        if skill.id == "ui_export" and state.get("page") != "编辑页":
            return f"剪映现在在{state.get('page')}，导出需要在编辑页打开一个草稿"
        if skill.id == "ui_open_draft" and state.get("page") == "编辑页":
            return "剪映现在在编辑页，先按 Ctrl+Alt+Q 回草稿页再打开别的草稿"
    return ""


def filter_steps(steps: list[dict], state: dict) -> tuple[list[dict], list[str]]:
    """没有草稿时，工程通道的步骤没有落脚点，先摘掉并说明。"""
    if state.get("draft"):
        return list(steps), []
    from . import skills

    kept: list[dict] = []
    dropped: list[str] = []
    for step in steps:
        skill = skills.get(step.get("skill", ""))
        if skill and skill.channel == "draft":
            dropped.append(f"{step.get('skill')}（没有草稿，工程通道无从下手）")
            continue
        kept.append(step)
    return kept, dropped
