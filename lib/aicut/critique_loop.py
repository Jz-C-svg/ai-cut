# -*- coding: utf-8 -*-
"""Critic 独立模块（TOP1 细化·生态共识版）。

把 review_film 的七维评分翻译成 Editor 可直接执行的 plan_patch 序列，
对齐 agentic-video-maker / EditDuet / VEA 等开源项目的 critic 输出 schema：

  {
    "overall_score": 7.5,           # 0-10 分制（review_film 0-100 /10）
    "issues": [
      {"severity": "P0"|"P1"|"P2",  # P0=必须修, P1=建议修, P2=可选
       "location": "片段2"|"0-3s"|"全片",
       "plan_patch": {"skill": "add_subtitle", "args": {...}},
       "reason": "为什么这是个问题"}
    ],
    "ready_to_ship": false           # overall_score>=8 且 P0=0 时为 true
  }

设计原则：
  · plan_patch.skill 必须在 skills.REGISTRY 里（防幻觉），不在则置 None
  · context isolation（UCAS 教训）：每次 critique 只读当前草稿，不累积历史截图/历史评分；
    review_film 内部若有缓存，这里不主动喂历史，保证"这一轮看到的就是这一版草稿"
  · review_film.py 仍是 Critic 的数据来源（七维分数 + 短板 advice），
    本模块只做 schema 转换 + 阈值判定，不重写评分逻辑
"""
from __future__ import annotations

import json
from pathlib import Path

from . import review_film, skills

# P 级阈值（基于 review_film 七维 0-100 分）
P0_MAX = 40      # 维度分 < 40 = P0（必须修）
P1_MAX = 60      # 维度分 < 60 = P1（建议修）；>= 60 且触发 notes = P2
READY_SCORE = 8.0  # 0-10 制；overall_score >= 8 且 P0=0 → ready_to_ship

# review_film severity → P 级
_SEV_TO_P = {"critical": "P0", "major": "P1", "minor": "P2"}


def _normalize_patch(skill_id: str, args: dict, sample: dict) -> dict | None:
    """校验 skill 在 REGISTRY 里；用 sample 补默认参数。不在 REGISTRY → None。"""
    if not skill_id or skill_id in ("manual", "DONE", "RENDER"):
        return None
    skill = skills.get(skill_id)
    if skill is None:
        return None
    merged = dict(args or {})
    for k, v in (sample or {}).items():
        merged.setdefault(k, v)
    return {"skill": skill_id, "args": merged}


def critique(draft, *, video=None, use_vision: bool = True, iteration: int = 1,
             task: str | None = None) -> dict:
    """对当前草稿做一轮 Critic 评审（无历史累积，context isolation）。

    参数：
      draft   草稿路径或 Draft 对象
      video   素材路径（缺省从草稿反推）
      use_vision  是否调镜头档案（False = 纯规则，快）
      iteration   当前迭代轮次（写进返回值，供 agent 记账）
      task    任务 id（durable state 用，本模块不直接写盘）

    返回：见模块 docstring 的 schema。
    """
    # context isolation：每次都是新调用，不复用旧 profile / 旧截图
    rf = review_film.review(draft, video=video, use_vision=use_vision)
    overall = round(float(rf.get("total", 0)) / 10.0, 1)  # 0-100 → 0-10

    issues: list[dict] = []
    for it in rf.get("issues", []):
        p = _SEV_TO_P.get(it.get("severity", "minor"), "P2")
        skill_id = it.get("suggested_action", "")
        skill = skills.get(skill_id) if skill_id else None
        patch = (_normalize_patch(skill_id, it.get("suggested_args") or {},
                                  skill.sample if skill else {})
                 if skill else None)
        issues.append({
            "severity": p,
            "location": it.get("location", "全片"),
            "plan_patch": patch,
            "reason": it.get("reason", ""),
        })

    # ---- v5.2.1 硬规则层：确定性评审（零 LLM）先跑，与 LLM 层合并 ----
    # 规则只读草稿 + 风格卡 + 素材元信息，可复现；LLM 层负责视觉/语义自由判断
    try:
        from . import rules_engine
        from .paths import SCRIPTS_DIR  # noqa: F401
        _sheet = None
        _draft_name = getattr(draft, "name", "") or ""
        _sheet_path = SCRIPTS_DIR / f"{_draft_name}.sheet.json"
        if _sheet_path.exists():
            try:
                _sheet = json.loads(_sheet_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                _sheet = None
        rule_issues = rules_engine.hard_rules(
            draft, video=video,
            style_id=(_sheet or {}).get("style") if _sheet else None,
            sheet=_sheet)
        # 合并：rule_id 去重（LLM 与规则重复时保留规则版），规则版无 rule_id
        seen_rules = {i.get("rule_id") for i in issues if i.get("rule_id")}
        for ri in rule_issues:
            if ri.get("rule_id") in seen_rules:
                continue
            seen_rules.add(ri.get("rule_id"))
            issues.append(ri)
    except Exception:  # noqa: BLE001 硬规则层失败不影响 LLM 评审主流程
        pass

    # 排序：P0 → P1 → P2（Editor 优先处理）
    order = {"P0": 0, "P1": 1, "P2": 2}
    issues.sort(key=lambda i: order.get(i.get("severity", "P2"), 3))

    p0 = [i for i in issues if i["severity"] == "P0"]
    ready = (overall >= READY_SCORE and not p0)

    # ---- 画面干净检查（cleancheck）注入：低分进 issue，驱动下一轮评审/升级人工 ----
    clean_info: dict | None = None
    if video:
        try:
            import cv2 as _cv2
            from . import cleancheck
            vp = Path(video)
            if vp.exists():
                cap = _cv2.VideoCapture(str(vp))
                if cap.isOpened():
                    cap.set(_cv2.CAP_PROP_POS_MSEC, 1000.0)  # 抽第 1 秒帧
                    ok, frame = cap.read()
                    cap.release()
                    if ok:
                        clean_info = cleancheck.check_frame(frame)
                        cs = float(clean_info.get("clean_score", 100))
                        if cs < 60:
                            issues.append({
                                "severity": "P0",
                                "location": "全片",
                                "plan_patch": None,
                                "reason": f"画面干净度 {cs:.1f}/100 不达标（{'；'.join(clean_info.get('issues') or []) or '背景杂乱/重影/异物'}），建议重拍或大幅处理后再评审",
                            })
                        elif cs < 75:
                            issues.append({
                                "severity": "P1",
                                "location": "全片",
                                "plan_patch": None,
                                "reason": f"画面干净度 {cs:.1f}/100 一般（{'；'.join(clean_info.get('issues') or []) or '轻微杂乱'}），建议微调后发布",
                            })
        except Exception:  # noqa: BLE001 干净检查失败不影响评审主流程
            clean_info = None

    p0 = [i for i in issues if i["severity"] == "P0"]
    ready = (overall >= READY_SCORE and not p0)

    return {
        "overall_score": overall,
        "issues": issues,
        "ready_to_ship": ready,
        "iteration": iteration,
        "ready_threshold": READY_SCORE,
        "cleancheck": clean_info,
        # 保留原始七维数据供追溯/调试（不影响 Editor 决策）
        "review_film": {
            "total": rf.get("total"),
            "weakest": rf.get("weakest"),
            "dimensions": {k: v.get("score") for k, v in (rf.get("dimensions") or {}).items()},
            "verdict_legacy": rf.get("verdict"),  # 旧字段，向后兼容
        },
    }


def p0_signature(issues: list[dict]) -> tuple:
    """P0 问题的"指纹"：(type, location) 列表。用于判断两轮之间 P0 是否有变化。"""
    return tuple((i.get("severity"), i.get("location"),
                  (i.get("plan_patch") or {}).get("skill"))
                 for i in issues if i.get("severity") == "P0")


def describe(result: dict) -> str:
    """一行人话，给 agent log 用。"""
    p0 = sum(1 for i in result["issues"] if i["severity"] == "P0")
    p1 = sum(1 for i in result["issues"] if i["severity"] == "P1")
    p2 = sum(1 for i in result["issues"] if i["severity"] == "P2")
    return (f"overall {result['overall_score']}/10 · P0={p0} P1={p1} P2={p2} · "
            f"ready={result['ready_to_ship']}")
