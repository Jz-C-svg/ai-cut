# -*- coding: utf-8 -*-
"""copilot_v2.py —— Codex 6.0 第二轮蒸馏（方法论层 v3.9.6）。

把 Codex 6 最新公开能力（2026-08/09 已联网查证）中与剪辑项目最契合的
三大能力蒸馏为本项目可复用的模块：

  A. Subagents / Multi-Agent V2 三角色编排
     —— orchestrator（主规划） / worker（执行） / explorer（探索检索），
        对应 Codex "orchestrator 可委派子代理、模型即工具"（2026-08-16 更新）
        与 Agents API "任务/模型/工具/环境 四要素解耦"（2026-09-10）。
  B. 偏好学习（Learning from previous actions）
     —— 捕获"用户修正"（用户手动改过的剪辑决策），转成可复用的偏好规则，
        下次同类素材自动注入规划。对应 Codex Memory 的偏好记忆能力。
  C. 计划队列与断点恢复（Future work scheduling）
     —— 把 Play Mode 出的步骤清单持久化为队列，每步完成后落盘，
        中断后从断点继续，未来可接定时调度。对应 Codex 长任务持久化与调度。

设计约束（与 copilot_method.py 一致）：
  · 只提供 prompt 模板（纯字符串）+ 规则函数 + 数据结构；
  · 不直接调用任何模型/路由，返回内容供 agent.py / llm_router 复用；
  · 不硬编码模型名；不修改 v3.8/v3.9/v4.0 既有文件。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .paths import WORK

# =============================================================================
# A. Subagents：三角色编排（orchestrator / worker / explorer）
# =============================================================================

# 角色定义：duty=职责，tools=可用工具子集（只约束提示词，不强制拦截），
# task_types=默认承接的任务类型（衔接 llm_router 的 task 路由）
ROLE_DEFS: dict[str, dict[str, Any]] = {
    "orchestrator": {
        "name": "orchestrator（主编排）",
        "duty": "读全局目标与素材/草稿现状，拆解任务、指派给 worker/explorer，"
                "汇总各方结果做最终剪辑决策；不亲自做素材分析或时间线操作。",
        "tools": ["plan_any", "skills.list", "critique_loop"],
        "task_types": ["plan", "decision"],
    },
    "worker": {
        "name": "worker（执行者）",
        "duty": "按 orchestrator 指派执行具体剪辑技能（切分/字幕/花字/配乐/导出等），"
                "一次只做一个动作，做完立即回报可观察结果。",
        "tools": ["clip", "text", "subtitle", "music", "export", "ui_*"],
        "task_types": ["edit"],
    },
    "explorer": {
        "name": "explorer（探索者）",
        "duty": "检索参考：素材语义档案、05-templates 模板、memory.sqlite 经验、"
                "notes.json 素材笔记、内容库；产出候选与证据，不直接改草稿。",
        "tools": ["semantic_profile", "template_match", "videofinger", "memory", "notes"],
        "task_types": ["semantic", "visual", "retrieval"],
    },
    "reviewer": {
        "name": "reviewer（审查者）",
        "duty": "评审产出（成片质量用 critique_loop，执行过程用 copilot_method.review_plan），"
                "输出问题清单与 plan_patch，驱动下一轮迭代。",
        "tools": ["cleancheck", "compare", "review_film", "critique_loop"],
        "task_types": ["quality", "review"],
    },
}

# 任务类型 → 默认角色路由表（llm_router 的 task_type 对齐）
TASK_ROLE_MAP: dict[str, str] = {
    "plan": "orchestrator",
    "decision": "orchestrator",
    "edit": "worker",
    "semantic": "explorer",
    "visual": "explorer",
    "retrieval": "explorer",
    "quality": "reviewer",
    "review": "reviewer",
    "text": "worker",          # 文本生成（字幕/文案）默认归执行者
}

# 各角色 prompt 模板（纯字符串；{...} 由调用方填充）
ROLE_PROMPTS: dict[str, str] = {
    "orchestrator": (
        "你是剪辑任务的 orchestrator（Codex Multi-Agent 编排）。\n"
        "【全局目标】{goal}\n【项目状态】{state}\n"
        "规则：不要亲自做素材理解与时间线操作；把任务委派给 worker/explorer，"
        "等它们回报后再做最终决策。输出 JSON："
        '{{"委派": [{{"角色":"worker|explorer", "任务":"...", "输入":"..."}}], '
        '"决策": "汇总后的最终剪辑方案"}}'
    ),
    "worker": (
        "你是剪辑 worker（Codex 子代理）。\n【指派任务】{task}\n【输入】{input}\n"
        "规则：一次只做一个动作，只调用被指派范围内的技能，做完立即回报可观察结果"
        "（时长差/片段数/草稿 diff）。输出 JSON：{{\"动作\":\"...\", \"结果\":\"...\", "
        '"是否达标": true|false, "偏差": "..."}}'
    ),
    "explorer": (
        "你是剪辑 explorer（Codex 子代理，只检索不改草稿）。\n【检索任务】{task}\n"
        "【可用来源】{sources}\n规则：产出候选清单与证据（来源/置信度），不要执行剪辑。"
        "输出 JSON：{{\"候选\": [{{\"项\":\"...\", \"来源\":\"...\", "
        '"置信度":0.0-1.0, "证据":"..."}}], "结论": "..."}}'
    ),
    "reviewer": (
        "你是剪辑 reviewer（Codex 审查子代理）。\n【审查对象】{target}\n"
        "【评审维度】{dimensions}\n输出 JSON：{{\"问题\": [{{\"严重级\":\"P0|P1|P2\", "
        '"描述":"...", "建议":"..."}}], "ready_to_ship": true|false, "总评":"..."}}'
    ),
}


def role_for(task_type: str) -> str:
    """任务类型 → 默认角色。未知类型归 orchestrator。"""
    return TASK_ROLE_MAP.get(task_type, "orchestrator")


def delegate(task_type: str, task: str, *, goal: str = "", state: str = "",
             input_: str = "", sources: str = "", dimensions: str = "",
             target: str = "") -> dict[str, str]:
    """按任务类型委派给角色，返回 {role, task_type, prompt}（prompt 已填充）。

    示例：delegate("semantic", "分析 IMG_5842 的画面语义")
          → {"role":"explorer", "task_type":"semantic", "prompt": ...}
    """
    role = role_for(task_type)
    if role == "orchestrator":
        prompt = ROLE_PROMPTS["orchestrator"].format(goal=goal or task, state=state or "(未提供)")
    elif role == "worker":
        prompt = ROLE_PROMPTS["worker"].format(task=task, input=input_ or "(未提供)")
    elif role == "explorer":
        prompt = ROLE_PROMPTS["explorer"].format(task=task, sources=sources or "semantic_profile, 05-templates, memory.sqlite, notes.json")
    else:  # reviewer
        prompt = ROLE_PROMPTS["reviewer"].format(
            target=target or task, dimensions=dimensions or "画面干净/节奏/结构/字幕/合规")
    return {"role": role, "task_type": task_type, "prompt": prompt}


def merge_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总各角色回报（统一协议）→ orchestrator 决策输入。

    入参元素（建议）：{"role": ..., "ok": bool, "payload": Any, "error": ""}
    返回：{"roles": [去重角色名], "succeeded": n, "failed": m,
          "payloads": {role: [payload...]}, "summary": "..."}
    """
    roles: list[str] = []
    payloads: dict[str, list[Any]] = {}
    ok_n = fail_n = 0
    for r in results or []:
        role = str(r.get("role", "unknown"))
        if role not in roles:
            roles.append(role)
        payloads.setdefault(role, [])
        if r.get("ok"):
            ok_n += 1
            payloads[role].append(r.get("payload"))
        else:
            fail_n += 1
            payloads[role].append({"error": str(r.get("error", ""))[:200]})
    return {
        "roles": roles,
        "succeeded": ok_n, "failed": fail_n,
        "payloads": payloads,
        "summary": f"子代理回报：成功 {ok_n} / 失败 {fail_n}（角色 {', '.join(roles) or '无'}）",
    }


# =============================================================================
# B. 偏好学习：用户修正捕获 → 偏好规则（memory.sqlite preferences）
# =============================================================================

@dataclass
class Correction:
    """一次用户/评审修正记录（学习信号）。"""
    material_type: str          # 素材类型（口播/母婴/风景/…）
    skill: str                  # 被修正的技能
    before: dict[str, Any]      # 修正前参数
    after: dict[str, Any]       # 修正后参数
    reason: str = ""            # 修正原因（用户话术/评审建议）
    source: str = "user"        # user / reviewer


def _param_diff(before: dict[str, Any], after: dict[str, Any]) -> str:
    """把参数差异转成人类可读的『调整描述』。"""
    keys = sorted(set(before) | set(after))
    parts = []
    for k in sorted(keys):
        b, a = before.get(k), after.get(k)
        if b != a:
            parts.append(f"{k}: {b} → {a}")
    return "；".join(parts) or "(参数未变，仅决策顺序/目标调整)"


def rule_from_correction(c: Correction) -> str:
    """修正 → 偏好规则文本（供 memory.add_preference 使用）。"""
    diff = _param_diff(c.before, c.after)
    base = f"素材类型『{c.material_type}』且技能『{c.skill}』时，参数调整为：{diff}"
    return f"{base}（依据：{c.reason or c.source} 修正）"


def capture_correction(mem, c: Correction, *, confidence: float = 0.6) -> dict[str, Any]:
    """把一次修正写入 memory.sqlite 的 preferences 表。

    mem 需为 memory.Memory 实例（open_memory()）。重复证据会提升 confidence：
    同类型同技能偏好已存在时，confidence 提升 0.15（上限 0.95）。
    返回 {"rule": ..., "confidence": ..., "recorded": bool, "note": "..."}
    """
    rule = rule_from_correction(c)
    recorded = False
    # 查同类型同技能的既有偏好，做证据累加
    for pref in mem.preferences(limit=200):
        if c.material_type in pref.get("rule", "") and c.skill in pref.get("rule", ""):
            confidence = min(0.95, float(pref.get("confidence", 0.6)) + 0.15)
            mem.add_preference(rule, evidence=f"再次修正（{c.source}）", confidence=confidence,
                               source=c.source or "reviewer")
            recorded = True
            break
    if not recorded:
        mem.add_preference(rule, evidence=f"首次修正（{c.source}）", confidence=confidence,
                           source=c.source or "reviewer")
    return {"rule": rule, "confidence": round(confidence, 2),
            "recorded": True, "note": "已写入 memory.sqlite preferences"}


def learned_adjustments(mem, material_type: str, skill: str | None = None,
                        min_confidence: float = 0.5) -> list[dict[str, Any]]:
    """查询适用于该素材类型的偏好调整（注入规划的输入）。

    返回 [{"rule", "confidence", "evidence", "skill"}]，按 confidence 降序。
    """
    out = []
    for pref in mem.preferences(limit=200):
        rule = str(pref.get("rule", ""))
        conf = float(pref.get("confidence", 0.0))
        if material_type in rule and conf >= min_confidence:
            if skill and skill not in rule:
                continue
            out.append({"rule": rule, "confidence": round(conf, 2),
                        "evidence": str(pref.get("evidence", "")),
                        "skill": skill})
    out.sort(key=lambda x: -x["confidence"])
    return out


# =============================================================================
# C. 计划队列：持久化 + 断点恢复（Future work scheduling 蒸馏）
# =============================================================================

@dataclass
class PlanQueue:
    """步骤队列：Play Mode 出的计划 → 可恢复队列。

    steps 每项含 序号/动作/参数/预期/验证方式（对齐 validate_plan schema），
    外加 done/result 运行时字段；done_indices 记录已完成序号，中断后可续跑。
    """
    task: str
    goal: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    done_indices: list[int] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    updated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))

    @property
    def next_index(self) -> int | None:
        """下一个未完成步骤的序号（1-based），全部完成返回 None。"""
        done = set(self.done_indices)
        for st in self.steps:
            idx = int(st.get("序号", st.get("index", 0)))
            if idx and idx not in done:
                return idx
        return None

    @property
    def progress(self) -> dict[str, Any]:
        total = len(self.steps)
        return {"done": len(self.done_indices), "total": total,
                "next": self.next_index,
                "remaining": sum(1 for st in self.steps
                                 if int(st.get("序号", st.get("index", 0))) not in set(self.done_indices))}


def queue_path(task: str) -> Path:
    """队列固定存 work/plan_queue/<task>.json（原子写）。"""
    d = WORK / "plan_queue"
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in task if ch.isalnum() or ch in "-_.")[:60] or "task"
    return d / f"{safe}.json"


def save_queue(q: PlanQueue) -> Path:
    """持久化队列（原子写：临时文件再替换）。"""
    q.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    p = queue_path(q.task)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(q), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p


def load_queue(task: str) -> PlanQueue | None:
    """读取队列；不存在/损坏返回 None，绝不抛错。"""
    p = queue_path(task)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return PlanQueue(**data)
    except Exception:  # noqa: BLE001
        return None


def mark_done(task: str, index: int, result: dict[str, Any] | None = None) -> dict[str, Any]:
    """标记某步完成（并把 result 写回该步），返回当前进度。"""
    q = load_queue(task)
    if q is None:
        return {"ok": False, "error": f"队列不存在：{task}"}
    if index not in q.done_indices:
        q.done_indices.append(index)
    for st in q.steps:
        if int(st.get("序号", st.get("index", 0))) == index and result is not None:
            st["result"] = result
    save_queue(q)
    return {"ok": True, "progress": q.progress, "task": task}


def resume(task: str) -> dict[str, Any]:
    """断点恢复信息：下一个未做步骤 + 已完成概览（供 agent 续跑）。"""
    q = load_queue(task)
    if q is None:
        return {"ok": False, "error": f"队列不存在：{task}", "task": task}
    prog = q.progress
    nxt = q.next_index
    step = next((s for s in q.steps if int(s.get("序号", s.get("index", 0))) == nxt), None)
    return {"ok": True, "task": task, "goal": q.goal, "progress": prog,
            "next_step": step, "next_index": nxt,
            "summary": f"进度 {prog['done']}/{prog['total']}，下一步："
                       f"{step.get('动作') if step else '(全部完成)'}"}


# =============================================================================
# 自评：第二轮蒸馏对照（Codex 6 最新能力）
# =============================================================================

ASSESSMENT_V2: list[dict[str, str]] = [
    {
        "ability": "Subagents 三角色编排（orchestrator/worker/explorer）",
        "status": "新增",
        "gap": "原单一 agent 顺序执行，无角色委派层；Editor-Critic 只是双角色雏形",
        "path": "subagents：ROLE_DEFS + delegate() + merge_results()，agent 可选接入",
    },
    {
        "ability": "偏好学习（Learning from previous actions）",
        "status": "新增",
        "gap": "memory.sqlite 有 preferences 表但无『用户修正→规则』学习封装",
        "path": "preference_learner：capture_correction() + learned_adjustments()",
    },
    {
        "ability": "计划队列与断点恢复（Future scheduling）",
        "status": "新增",
        "gap": "Play Mode 计划仅存内存，中断后丢失；无持久化队列",
        "path": "plan_queue：save/load/mark_done/resume，落 work/plan_queue/",
    },
]


def self_assessment_v2() -> list[dict[str, str]]:
    """返回第二轮三能力对照表。"""
    return [dict(r) for r in ASSESSMENT_V2]


__all__ = [
    "ROLE_DEFS", "TASK_ROLE_MAP", "ROLE_PROMPTS", "role_for", "delegate",
    "merge_results",
    "Correction", "rule_from_correction", "capture_correction", "learned_adjustments",
    "PlanQueue", "queue_path", "save_queue", "load_queue", "mark_done", "resume",
    "self_assessment_v2", "ASSESSMENT_V2",
]
