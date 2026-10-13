# -*- coding: utf-8 -*-
"""copilot_method.py —— Codex 6.0 Agent 工作法蒸馏模块（v3.9.5 方法论层）。

把 Codex CLI 公开的 Agent 循环方法论（Play / Review / Context-Notes /
Terminal 长任务循环 / 桌面 Agentic 操作）蒸馏成本项目可复用的五个能力模块。
本模块**只提供提示词模板（纯字符串）+ 规则函数 + 数据结构**，不直接调用任何
模型路由（llm_router/llm），返回的 prompt 字符串供未来 llm_router / agent 复用，
不硬编码任何模型名。

五项能力与主循环对接位置（agent.py）：
  感知(perceive) →【插入 play_mode 规划门】→ 规划(plan_any)
                 → 执行(_execute_step，失败走 terminal_loop 自纠错)
                 → 验证(verify/critique_loop 成片质量)
                 →【插入 review_mode 执行过程合规审查】→ 记账(memory.sqlite)

与既有模块的分工：
  · critique_loop.py / review_film.py —— 管【成片质量】评审（画面/节奏/结构好不好）
  · copilot_method.review_mode      —— 管【执行过程】合规审查（计划 vs 已执行差异）
  · memory.py (memory.sqlite)       —— 管经验记忆（技能成功率/偏好/事实）
  · copilot_method.context_notes    —— 管素材级/会话级结构化笔记（notes.json），互补
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .paths import WORK

# 自纠错默认上限（Codex Terminal 长任务 Agent：一次动作→观察→自纠错，最多 N 次）
DEFAULT_MAX_RETRIES = 3


# =============================================================================
# 一、提示词模板库（规划 / 执行 / 评审 / 纠错，纯字符串，可被 llm_router 复用）
# =============================================================================

# ① Play Mode 规划 prompt：先读项目 → 分析结构 → 出实施步骤清单 → 再动手
PLAY_MODE_PROMPT = """你是本项目的资深剪辑 Agent（Codex Play Mode 工作法）。
铁律：**不直接开干**。先读项目、分析结构，再出实施步骤清单，确认每步可验证后才动手。

【任务目标】
{goal}

【项目结构摘要】
{structure_summary}

【素材/草稿现状】
{state_summary}

请严格按以下 JSON 输出实施步骤清单（不要输出 JSON 以外的内容）：
{{
  "理解": "一句话复述你对目标的理解",
  "步骤": [
    {{
      "序号": 1,
      "动作": "要调用的技能名或操作（必须落在本项目已有技能上，不得发明新工具）",
      "参数": {{"...": "..."}},
      "预期": "这一步做完后草稿/素材应该变成什么样（可观察的变化）",
      "验证方式": "用什么检查预期是否达成（时长差/草稿 diff/断言/人工确认）"
    }}
  ],
  "风险": ["预计可能踩的坑"],
  "回滚点": "哪一步之前快照，失败可一键回退"
}}
"""

# ② 执行 prompt：长任务单步执行
EXECUTE_PROMPT = """你在执行剪辑长任务的第 {step_no}/{total} 步。
【本步计划】动作={action} 参数={args}
【预期结果】{expected}
【上一步观察】{last_observation}
规则：一次只做这一个动作；做完立即观察结果，不要凭感觉判断成功。
"""

# ③ 评审 prompt：对照计划审查单步执行结果
JUDGE_PROMPT = """对照计划审查这一步是否达标。
【计划预期】{expected}
【实际观察】{observation}
【验证方式】{verify_method}
请只回答三选一：达标 / 未达标(说明偏差) / 无法判断(缺什么证据)。
"""

# ④ 纠错 prompt：自纠错——必须先读错误信息，再改方案
REPAIR_PROMPT = """这一步未达标，进入自纠错（第 {retry_no}/{max_retries} 次）。
铁律：**先完整读错误信息，再改方案**，禁止盲目重试同一动作。
【原动作】{action} 参数={args}
【错误信息】{error}
【已试过的方案】{tried}
请给出修正后的参数或新动作，并说明这次为什么和上次不同。
"""

# 模板库汇总键（供 llm_router/agent 按名取用）
PROMPTS: dict[str, str] = {
    "play_mode": PLAY_MODE_PROMPT,
    "execute": EXECUTE_PROMPT,
    "judge": JUDGE_PROMPT,
    "repair": REPAIR_PROMPT,
}


# =============================================================================
# 二、Play Mode：规划门
# =============================================================================

def play_mode() -> dict[str, Any]:
    """返回 Play Mode 能力包：prompt 模板 + 规则函数说明 + 步骤 schema。

    对接 agent.py：在 感知(perceive) 与 规划(plan_any) 之间插入"规划门"——
    先调用 build_plan_request() 生成规划 prompt 交给模型，模型产出步骤清单 JSON 后
    用 validate_plan() 校验每步是否含 动作/预期/验证方式，不齐则打回重规划。
    """
    return {
        "name": "play_mode",
        "rule": "先读项目结构 → 分析 → 出实施步骤清单 → 再动手，禁止直接开干",
        "prompt": PLAY_MODE_PROMPT,
        "step_schema": {
            "序号": "int，从 1 开始",
            "动作": "技能名/操作，必须命中本项目已有技能",
            "参数": "dict",
            "预期": "可观察的变化描述",
            "验证方式": "时长差/diff/断言/人工确认",
        },
        "integration_point": "agent.py run()：perceive() 之后、plan_mod.plan_any() 之前",
    }


def build_plan_request(goal: str, structure_summary: str = "",
                       state_summary: str = "") -> str:
    """把目标+项目结构摘要格式化成 Play Mode 规划 prompt（纯字符串返回）。"""
    return PLAY_MODE_PROMPT.format(
        goal=goal or "(未指定)",
        structure_summary=structure_summary or "(未提供，需先读项目结构)",
        state_summary=state_summary or "(未提供，需先感知草稿/素材现状)",
    )


def validate_plan(plan: dict[str, Any]) -> tuple[bool, list[str]]:
    """规划门规则：检查步骤清单每步是否含 动作/预期/验证方式。

    返回 (是否通过, 问题清单)。不阻断主流程，只输出警告，交由 agent log。
    """
    issues: list[str] = []
    steps = plan.get("步骤") or plan.get("steps") or []
    if not steps:
        return False, ["步骤清单为空：Play Mode 要求先出步骤再动手"]
    for i, st in enumerate(steps, 1):
        if not st.get("skill") and not st.get("动作"):
            issues.append(f"第 {i} 步缺『动作』")
        if not st.get("预期"):
            issues.append(f"第 {i} 步缺『预期』（做完应变成什么样）")
        if not st.get("验证方式"):
            issues.append(f"第 {i} 步缺『验证方式』")
    return (not issues), issues


# =============================================================================
# 三、Review Mode：执行过程合规审查（与 critique_loop 分工）
# =============================================================================

def review_mode() -> dict[str, Any]:
    """返回 Review Mode 能力包说明。

    分工（重要，避免和现有评审重复建设）：
      · critique_loop.py / review_film.py：Editor-Critic，管【成片质量】
        —— 画面干不干净、节奏对不对、结构满不满足七维评分，输出 plan_patch 驱动下一轮剪辑。
      · review_mode：管【执行过程合规】—— 独立审查"计划 vs 已执行"的差异，
        输出问题清单（遗漏/偏差/风险），不关心片子美不美，只关心有没有按计划做、
        有没有跑偏、有没有隐藏风险。
    """
    return {
        "name": "review_mode",
        "rule": "独立审查未提交改动：读计划 vs 已执行差异，输出问题清单",
        "prompt": JUDGE_PROMPT,
        "division_of_labor": (
            "critique_loop=成片质量评审；review_mode=执行过程合规审查（本模块）"
        ),
    }


def _step_key(skill: str, args: dict) -> tuple:
    return (skill, tuple(sorted((args or {}).items())))


def review_plan(plan: dict[str, Any], executed: list[Any]) -> dict[str, Any]:
    """读计划 vs 已执行差异，输出问题清单（遗漏/偏差/风险）。

    参数：
      plan     plan_mod 产出的计划（取 plan["步骤"]）
      executed self.steps 中的 StepResult 列表（也兼容 dict：{skill,args,ok,...}）

    返回：
      {"issues": [{"type":"遗漏|偏差|风险", "detail":..., "severity":"P0|P1|P2"}],
       "summary": "..."}
    """
    planned = plan.get("步骤") or plan.get("steps") or []
    planned_keys = {_step_key(str(s.get("skill", "")), s.get("args") or {}) for s in planned}

    issues: list[dict[str, Any]] = []
    done_keys: set[tuple] = set()
    for ex in executed:
        if isinstance(ex, dict):
            skill = str(ex.get("skill", "")); args = ex.get("args") or {}
            ok = bool(ex.get("ok")); attempts = int(ex.get("attempts") or 1)
            error = str(ex.get("error") or ex.get("note") or "")
        else:  # StepResult
            skill = str(getattr(ex, "skill", "")); args = getattr(ex, "args", {}) or {}
            ok = bool(getattr(ex, "ok", False))
            attempts = int(getattr(ex, "attempts", 1))
            error = str(getattr(ex, "error", "") or getattr(ex, "note", ""))
        key = _step_key(skill, args)
        done_keys.add(key)
        # 偏差：执行了计划外的动作（如 Critic 修复步骤）
        if planned and key not in planned_keys:
            issues.append({"type": "偏差", "severity": "P2",
                           "detail": f"计划外动作：{skill} {json.dumps(args, ensure_ascii=False)[:80]}"})
        # 风险：失败或反复重试
        if not ok:
            issues.append({"type": "风险", "severity": "P0",
                           "detail": f"步骤失败：{skill} — {error[:120]}"})
        elif attempts > 1:
            issues.append({"type": "风险", "severity": "P1",
                           "detail": f"步骤 {skill} 重试 {attempts} 次才通过（可能参数/逻辑不稳）"})

    # 遗漏：计划了但没执行
    missing = planned_keys - done_keys
    for mk in missing:
        issues.append({"type": "遗漏", "severity": "P1",
                       "detail": f"计划步骤未执行：{mk[0]} {json.dumps(dict(mk[1]), ensure_ascii=False)[:80]}"})

    sev_rank = {"P0": 0, "P1": 1, "P2": 2}
    issues.sort(key=lambda x: sev_rank.get(x["severity"], 9))
    p0 = sum(1 for i in issues if i["severity"] == "P0")
    summary = f"审查完成：遗漏={sum(1 for i in issues if i['type']=='遗漏')}，" \
              f"偏差={sum(1 for i in issues if i['type']=='偏差')}，" \
              f"风险 P0={p0}"
    return {"issues": issues, "summary": summary, "ready": p0 == 0}


# =============================================================================
# 四、Context-Notes：跨上下文窗口持久笔记（notes.json）
# =============================================================================

@dataclass
class MaterialNote:
    """素材级笔记：每个素材一份，跨会话可检索。"""
    material_id: str                 # 素材指纹/文件名
    key_decisions: list[str] = field(default_factory=list)   # 关键决策
    template_source: str = ""        # 命中的模板来源（衔接 05-templates）
    history_fixes: list[str] = field(default_factory=list)   # 历史修正记录


@dataclass
class SessionNote:
    """会话级笔记：每个任务一份。"""
    task: str
    goal: str = ""
    global_decisions: list[str] = field(default_factory=list)   # 全局决策
    failure_retries: list[dict] = field(default_factory=list)    # 失败重试记录


def notes_path() -> Path:
    """notes.json 固定存 work/agent-state/ 下（与 durable state 同目录）。"""
    d = WORK / "agent-state"
    d.mkdir(parents=True, exist_ok=True)
    return d / "notes.json"


def load_notes() -> dict[str, Any]:
    """读取 notes.json；不存在或损坏返回空结构，绝不抛错。"""
    p = notes_path()
    if not p.exists():
        return {"materials": {}, "sessions": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {"materials": {}, "sessions": {}}
    data.setdefault("materials", {})
    data.setdefault("sessions", {})
    return data


def save_notes(data: dict[str, Any]) -> Path:
    """整体写回 notes.json（原子写：先临时文件再替换）。"""
    p = notes_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p


def merge_material_note(note: MaterialNote) -> Path:
    """合并素材笔记：新决策/修正追加到既有记录，不覆盖历史。"""
    data = load_notes()
    bucket: dict = data["materials"].setdefault(note.material_id, {
        "key_decisions": [], "template_source": "", "history_fixes": []})
    for d in note.key_decisions:
        if d and d not in bucket["key_decisions"]:
            bucket["key_decisions"].append(d)
    if note.template_source:
        bucket["template_source"] = note.template_source
    for f in note.history_fixes:
        if f and f not in bucket["history_fixes"]:
            bucket["history_fixes"].append(f)
    bucket["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return save_notes(data)


def merge_session_note(note: SessionNote) -> Path:
    """合并会话笔记：全局决策追加、失败重试记录追加。"""
    data = load_notes()
    bucket: dict = data["sessions"].setdefault(note.task, {
        "goal": "", "global_decisions": [], "failure_retries": []})
    if note.goal:
        bucket["goal"] = note.goal
    for d in note.global_decisions:
        if d and d not in bucket["global_decisions"]:
            bucket["global_decisions"].append(d)
    for r in note.failure_retries:
        bucket["failure_retries"].append(r)
    bucket["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return save_notes(data)


# 与既有体系的衔接说明（供 agent.py / 文档引用）：
CONTEXT_NOTES_LINKS = (
    "1) 模板库衔接：MaterialNote.template_source 记录命中的 05-templates 模板 id，"
    "下次同素材优先复用；2) 记忆化衔接：memory.py(memory.sqlite) 存的是跨任务经验"
    "（技能成功率/偏好规则），context_notes 存的是素材级/会话级结构化决策流水，"
    "二者互补不互相替代——经验记忆看‘哪类动作成功率高’，笔记看‘这个素材当时怎么定的’。"
)


# =============================================================================
# 五、Terminal 长任务工具循环：动作→观察→判断→自纠错
# =============================================================================

@dataclass
class LoopOutcome:
    ok: bool
    attempts: int
    observation: str = ""
    error: str = ""


def terminal_loop(max_retries: int = DEFAULT_MAX_RETRIES) -> dict[str, Any]:
    """返回长任务循环规范说明 + 执行/评审/纠错三类 prompt。"""
    return {
        "name": "terminal_loop",
        "rule": "一次动作 → 观察结果 → 判断是否达标 → 达标则下一步，否则自纠错；"
                f"自纠错最多 {max_retries} 次；每次纠错必须先读错误信息再改方案",
        "max_retries": max_retries,
        "prompts": {"execute": EXECUTE_PROMPT, "judge": JUDGE_PROMPT, "repair": REPAIR_PROMPT},
    }


def run_loop(act: Callable[[], dict],
             observe: Callable[[dict], tuple[bool, str]],
             *,
             max_retries: int = DEFAULT_MAX_RETRIES,
             step_no: int = 1, total: int = 1,
             action_name: str = "",
             expected: str = "",
             verify_method: str = "",
             log: Callable[[str], None] = print) -> LoopOutcome:
    """通用长任务单步循环执行器（动作→观察→达标？→自纠错）。

    参数：
      act()      执行一次动作，返回动作结果 dict（供 observe 使用）
      observe(res) → (是否达标, 观察描述)；观察描述里必须包含错误信息原文
      max_retries 自纠错上限（默认 3）
      log        日志回调

    自纠错铁律：第 N 次重试前，log 里强制带上上一次的错误观察，再允许换方案。
    """
    last_error = ""
    tried: list[str] = []
    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            # 自纠错：先读错误，再改方案；act 的实现方应基于 last_error 调整
            log(REPAIR_PROMPT.format(
                retry_no=attempt, max_retries=max_retries,
                action=action_name, args="(见 act 实现)",
                error=last_error[:300],
                tried="；".join(tried[-3:]) or "(无)"))
        res = act()
        ok, observation = observe(res)
        tried.append(observation[:80])
        if ok:
            return LoopOutcome(ok=True, attempts=attempt, observation=observation)
        last_error = observation
        log(f"  [terminal_loop] 第 {attempt}/{max_retries} 次未达标：{observation[:160]}")
    return LoopOutcome(ok=False, attempts=max_retries,
                       observation=observation, error=last_error)


# =============================================================================
# 六、Self-Assessment：本项目 vs Codex 6.0 五项能力自评表
# =============================================================================

# 状态取值：已具备 / 部分具备 / 缺失
ASSESSMENT_TABLE: list[dict[str, str]] = [
    {
        "ability": "Play Mode（先规划后动手）",
        "status": "部分具备",
        "gap": "plan_any 出步骤但无‘预期/验证方式’字段，规划门校验缺位",
        "path": "play_mode() + validate_plan() 插入 agent.py 感知→规划之间",
    },
    {
        "ability": "Review Mode（计划 vs 执行差异审查）",
        "status": "缺失",
        "gap": "现有 critique_loop 只管成片质量，无执行过程合规审查",
        "path": "review_mode.review_plan() 收尾时输出遗漏/偏差/风险清单",
    },
    {
        "ability": "Context-Notes（跨窗口持久笔记）",
        "status": "部分具备",
        "gap": "memory.sqlite 有经验记忆，但素材级决策/模板来源无结构化笔记",
        "path": "context_notes：MaterialNote/SessionNote 落 work/agent-state/notes.json",
    },
    {
        "ability": "Terminal 长任务循环（动作-观察-自纠错）",
        "status": "部分具备",
        "gap": "_execute_step 有重试+回滚，但无‘先读错误再改方案’的规范与上限约束",
        "path": "terminal_loop.run_loop() 包装，max_retries=3，纠错必先读错误",
    },
    {
        "ability": "桌面 Agentic 操作（观察界面→操作→验证）",
        "status": "部分具备",
        "gap": "vision/perception 已能观察剪映草稿与素材画面，capcut_auto 可操作，"
               "但未形成‘观察剪映界面→操作→验证’显式闭环",
        "path": "复用 perception+capcut_auto，按 terminal_loop 范式显式化三步循环",
    },
]


def self_assessment() -> list[dict[str, str]]:
    """返回五项能力对照表（已具备/部分具备/缺失 + 落地路径）。"""
    return [dict(row) for row in ASSESSMENT_TABLE]


def render_assessment_report() -> str:
    """渲染成人话 Markdown 自评报告（供 docs / agent log 使用）。"""
    lines = ["# 本项目 vs Codex 6.0 五项能力自评表", ""]
    lines.append("| 能力 | 状态 | 差距 | 落地路径 |")
    lines.append("|---|---|---|---|")
    for r in ASSESSMENT_TABLE:
        lines.append(f"| {r['ability']} | {r['status']} | {r['gap']} | {r['path']} |")
    counts = {"已具备": 0, "部分具备": 0, "缺失": 0}
    for r in ASSESSMENT_TABLE:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines += ["", f"**结论**：已具备 {counts['已具备']} 项，部分具备 {counts['部分具备']} 项，"
                  f"缺失 {counts['缺失']} 项。优先级：Review Mode（缺失）> Play Mode 规划门 "
                  "> Terminal 自纠错规范 > Context-Notes 结构化 > 桌面操作显式闭环。"]
    return "\n".join(lines)


__all__ = [
    "PROMPTS", "play_mode", "build_plan_request", "validate_plan",
    "review_mode", "review_plan",
    "MaterialNote", "SessionNote", "notes_path", "load_notes", "save_notes",
    "merge_material_note", "merge_session_note", "CONTEXT_NOTES_LINKS",
    "terminal_loop", "run_loop", "LoopOutcome",
    "self_assessment", "render_assessment_report", "ASSESSMENT_TABLE",
    "DEFAULT_MAX_RETRIES",
]
