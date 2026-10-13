# -*- coding: utf-8 -*-
"""成片评审技能（2.0 升级二）：接入技能表，供 agent 在闭环末尾调用。"""
from __future__ import annotations

from . import review_film, skills


def skill_review_film(ctx: skills.Context, args: dict) -> dict:
    """对当前草稿做七维评审，返回总分 + 各维 + 最短板建议。"""
    target = ctx.draft
    if target is None:
        return {"op": "review_film", "ok": False, "error": "没有草稿可评审"}
    res = review_film.review(target, video=ctx.video, use_vision=True)
    return {
        "op": "review_film",
        "ok": True,
        "total": res["total"],
        "dimensions": {k: v["score"] for k, v in res["dimensions"].items()},
        "weakest": res["weakest"],
        "note": (f"评审 {res['draft']}：总分 {res['total']}/100，"
                 f"最短板 {res['weakest']['dimension']}（{res['weakest']['score']}）："
                 f"{res['weakest']['advice']}"),
    }


skills.register(skills.Skill(
    id="review_film", goal="对当前成片做七维评审（结构/钩子/节奏/画面/字幕/声音/合规），给出短板与修复建议",
    channel="query", args={}, sample={}, fn=skill_review_film,
))
