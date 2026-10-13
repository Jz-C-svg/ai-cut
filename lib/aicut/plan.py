r"""规划器：把"人话目标"变成一串可执行、可验证的技能调用（EDL 意图序列）。

两种模式：
  llm    云端强模型读懂目标 + 当前草稿状态 + 你的偏好，从技能表里挑步骤（慢思考）
  rules  正则兜底，断网或没 key 时也能剪（覆盖最常见的几种说法）

规划器不能发明技能：只允许从 skills.REGISTRY 里挑，参数必须齐全。这样每一步都能
被验证、被回滚、被统计成功率——"聪明"建立在"不许乱来"之上。
"""
from __future__ import annotations
from aicut.portability import portable_path

import json
import re
from pathlib import Path

from . import llm, skills
from . import skills_material  # noqa: F401
from . import skills_maker  # noqa: F401
from . import architecture as arch_mod
from .paths import CONTENT_LIBRARY

SYSTEM = """你是短视频剪辑台的主控大脑。你面对素材、已有决策单或已经打开的剪映草稿，要把用户的一句话
目标翻译成一串可直接执行的步骤。你只能使用给定的技能表，不许发明新技能、不许输出
技能表以外的操作。参数必须能从目标或给定状态里确定，不能确定的就不要写这一步。

输出 JSON，格式：
{
  "理解": "一句话复述你打算做什么",
    "证据": ["引用当前状态中支持决策的事实"],
    "假设": ["必须成立但当前不完全确定的前提"],
    "不确定性": ["可能导致方案改变的风险"],
    "备选方案": [{"方案": "另一种做法", "取舍": "为什么没有选它"}],
    "选择理由": "为什么当前方案最适合这个目标",
  "步骤": [{"skill": "技能id", "args": {...}, "why": "十个字内"}],
    "验收": "一句话说明怎么算完成",
    "停止条件": "什么情况下必须停下来交给用户"
}

原则：
- 顺序：先删停顿/口误，再压缩到目标时长；生成成片的步骤单独执行。
- 只有素材时，用 generate_draft 走完整理解和规划流水线，再等待用户确认；不要猜素材路径或绕过质量检查。
- 时间都用秒，保留两位小数。
- 删停顿用 delete_silences；删口误用 delete_ai_cuts（需要识别结果）；压时长用 trim_to。
- 如果目标里没有明确的时间区间，就不要用 delete_range 猜数字。
- 先根据状态中的证据判断，再选择技能；不要把猜测写成事实。
- 至少给出一个关键不确定性；没有不确定性时明确写“无”。
- 备选方案只写真正可执行或有意义的方案，最多 3 个。
- 如果缺少素材、逐字稿、草稿或关键参数，停止条件必须明确要求补充，不要假装完成。
- 制作接口：已有决策单时先用 inspect_video_plan 检查；有应用明确记录确认的请求ID时才用 make_video。
- make_video 只能传 request_id，确认由应用提供，不得在模型参数中捏造确认哈希。
- 草稿生成、独立MP4和剪辑软件内导出是不同结果，必须沿用制作接口返回的状态。
- 当前制作能力：project支持jianying/capcut；portable为FFmpeg独立MP4；所有native软件内自动导出尚未验收。不要选择不支持的能力。
"""


def available_styles() -> list[dict]:
    path = CONTENT_LIBRARY / "styles.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    items = data.get("styles") if isinstance(data, dict) else data
    out = []
    for s in items or []:
        out.append({"id": s.get("id"), "名称": s.get("名称") or s.get("name")})
    return out


GUESS = [
    (r"口播|知识|讲解|干货", "koubo-zhishi"),
    (r"空间|探店|房型|看房", "kongjian-zhanshi"),
    (r"情绪|故事|叙事", "qingxu-gushi"),
    (r"对比|测评|横评", "duibi-ceping"),
    (r"卡点|节奏|炫", "kadian-jinji"),
    (r"图文|快闪|信息密度", "wenzi-kuaishan"),
    (r"vlog|日常|生活", "jiating-richang"),
    (r"盘点|合集|TOP|top", "zhuanti-pandian"),
]


def guess_style(goal: str, default: str = "koubo-zhishi") -> str:
    for pattern, style in GUESS:
        if re.search(pattern, goal, re.I):
            return style
    return default


def state_brief(state: dict) -> str:
    """把状态压成模型看得懂的短文本（别把整份草稿塞进去）。"""
    lines = []
    if state.get('video'):
        lines.append('当前素材：' + str(state['video']))
    draft = state.get("draft") or {}
    if draft:
        lines.append(f"草稿：{draft.get('name')} · 时长 {draft.get('duration', 0):.2f}s · 轨道 {draft.get('tracks')}")
        for seg_line in (draft.get("segments") or [])[:16]:
            lines.append("  " + seg_line)
    if state.get("silences"):
        spans = "、".join(f"{a:.2f}-{b:.2f}s" for a, b in state["silences"][:12])
        lines.append(f"检测到的停顿：{spans}")
    if state.get("vision_summary"):
        vs = state["vision_summary"]
        lines.append(f"画面：{vs.get('shots')} 个镜头，平均 {vs.get('avg_shot_len')}s；" + "；".join(vs.get("hints", [])[:4]))
    if state.get("has_transcript"):
        lines.append("有逐字稿")
    if state.get("has_understanding"):
        lines.append("有 AI 识别结果（可用于删口误）")
    if state.get('production'):
        lines.append('制作接口上下文：' + json.dumps(state['production'], ensure_ascii=False))
    if state.get("preferences"):
        lines.append("必须遵守的偏好：" + "；".join(state["preferences"]))
    if state.get("similar_cases"):
        lines.append("相似历史案例（few-shot 参考）：")
        lines.extend("  " + c for c in state["similar_cases"])
    if state.get("styles"):
        lines.append("可用风格：" + "、".join(f"{s['id']}({s.get('名称')})" for s in state["styles"]))
    if state.get("architectures"):
        lines.append("可用内容架构：" + "、".join(f"{a['id']}({a.get('名称')})" for a in state["architectures"]))
    if state.get("matched_architecture"):
        ma = state["matched_architecture"]
        lines.append(f"已匹配架构：{ma.get('id')}({ma.get('名称')})，匹配方法={ma.get('method', '?')}，"
                     f"得分={ma.get('matched', {}).get('得分', '?') if isinstance(ma.get('matched'), dict) else '?'}")
    # 目标平台信息注入（v1.0）：让规划/意图阶段知道发到哪个平台、要遵守什么口味
    if state.get("platform"):
        try:
            from . import platform_calibrator as _pc
            _cal = _pc.get_calibration(state["platform"])
            lines.append(
                f"目标平台：{_cal.get('平台名称')}（{_cal['platform_id']}）｜画幅 {_cal.get('画幅')}｜"
                f"推荐时长 {_cal.get('推荐甜点区')}｜平均镜头 {_cal.get('平均镜头时长目标')}｜"
                f"钩子 {_cal.get('钩子密度')}｜首钩 {_cal.get('首钩位置')}｜"
                f"BGM {_cal.get('BGM音量')}｜标题≤{_cal.get('标题字数上限')}字｜"
                f"违禁词收录 {len(_cal.get('违禁词列表', []))} 个")
        except Exception:
            lines.append(f"目标平台：{state['platform']}")
    lines.extend(knowledge_lines(state))
    return "\n".join(lines)


def build_messages(goal: str, state: dict) -> list[dict]:
    from .learning_context import retrieve, prompt_context
    try:
        bundle = retrieve(goal + ' ' + str(state.get('draft', {}).get('name', '')), budget=7000)
        learned = prompt_context(bundle)
    except (OSError, ValueError) as exc:
        learned = f'\n学习库检索暂不可用：{type(exc).__name__}；不得假称已参考学习库。'
    user = (
        f"目标：{goal}\n\n"
        f"可用技能：\n{skills.catalog_text()}\n\n"
        f"当前状态：\n{state_brief(state)}\n\n"
        f"{learned}\n请给出 JSON。"
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def validate(plan: dict) -> tuple[dict, list[str]]:
    """挡住模型乱写：技能必须存在、参数必须齐全。"""
    warnings: list[str] = []
    steps = []
    for raw in plan.get("步骤") or []:
        sid = (raw or {}).get("skill") or (raw or {}).get("技能")
        skill = skills.get(sid) if sid else None
        if not skill:
            warnings.append(f"忽略未知技能：{sid}")
            continue
        args = raw.get("args") or raw.get("参数") or {}
        missing = [k for k in skill.args if k in skill.sample and k not in args]
        steps.append({"skill": sid, "args": args, "why": raw.get("why") or raw.get("原因") or "",
                      "missing": missing})
    plan["步骤"] = steps
    if not steps:
        warnings.append("规划结果里没有可执行步骤")
    return plan, warnings


def plan_rules(goal: str, state: dict) -> dict:
    """断网兜底：覆盖最常见的几种说法。"""
    steps: list[dict] = []
    production = state.get('production') or {}
    if production.get('request_id'):
        steps.append({'skill': 'make_video', 'args': {'request_id': production['request_id']},
                      'why': '调用已准备制作请求'})
    elif production.get('sheet'):
        args = {k: production[k] for k in ('sheet', 'software', 'engine', 'output', 'publish')
                if k in production and production[k] is not None}
        steps.append({'skill': 'inspect_video_plan', 'args': args, 'why': '检查决策单并等待确认'})
    if steps:
        return {'理解': goal, '来源': 'rules:production-api', '步骤': steps,
                '证据': ['应用提供了制作接口上下文'], '不确定性': ['产物仍需按接口报告验收'],
                '验收': '读取实际制作报告，不把草稿称为软件内导出成功',
                '停止条件': '等待确认、能力不支持或接口报错时停止'}
    num = r"(\d+(?:\.\d+)?)"
    m = re.search(r"(?:开头|前)\s*" + num + r"\s*秒", goal)
    if m:
        steps.append({"skill": "delete_head", "args": {"seconds": float(m.group(1))}, "why": "删开头"})
    m = re.search(r"(?:结尾|末尾|最后)\s*" + num + r"\s*秒", goal)
    if m:
        steps.append({"skill": "delete_tail", "args": {"seconds": float(m.group(1))}, "why": "删结尾"})
    if re.search(r"静音|停顿|空白|空档|喘气", goal):
        steps.append({"skill": "delete_silences", "args": {}, "why": "删停顿"})
    if re.search(r"口误|废话|重复|说错", goal):
        steps.append({"skill": "delete_ai_cuts", "args": {}, "why": "删口误"})
    if re.search(r"打点|实时.*(切|剪|编辑)|镜头切分|实时联动|program_edits", goal):
        # 实时打点链路：ai-auto-editor 识别 → 打点 JSON → apply_realtime_marks 切分草稿
        # marks_path 指向 ai-auto-editor 的 program_edits 目录，技能内部取最新文件
        steps.append({"skill": "apply_realtime_marks",
                      "args": {"marks_path": portable_path('user:AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/.user_skills/ai-auto-editor/data/program_edits'),
                               "delete_low": False},
                      "why": "按实时打点切分镜头"})
    m = re.search(r"(?:剪到|压到|控制在|做成|压成)\s*" + num + r"\s*秒", goal)
    if m:
        steps.append({"skill": "trim_to", "args": {"seconds": float(m.group(1))}, "why": "压时长"})
    m = re.search(num + r"\s*秒?\s*(?:到|至|-|~|—)\s*" + num + r"\s*秒", goal)
    if m and not any(s["skill"] in ("delete_head", "delete_tail") for s in steps):
        steps.append({"skill": "delete_range",
                      "args": {"t0": float(m.group(1)), "t1": float(m.group(2))}, "why": "删区间"})
    if re.search(r"出片|生成|剪一条|按.{0,8}型|风格", goal) and not steps:
        steps.append({"skill": "generate_draft",
                      "args": {"style": guess_style(goal)}, "why": "按风格出片"})
    evidence = []
    if state.get("draft"):
        evidence.append(f"已有草稿，当前时长 {float(state['draft'].get('duration', 0)):.2f}s")
    if state.get("silences"):
        evidence.append(f"检测到 {len(state['silences'])} 处停顿")
    if state.get("has_transcript"):
        evidence.append("已有逐字稿")
    if not evidence:
        evidence.append("仅依据用户目标进行规则匹配")
    uncertainty = []
    if not state.get("draft"):
        uncertainty.append("当前没有草稿，工程编辑步骤需要先准备素材")
    if not state.get("has_transcript"):
        uncertainty.append("没有逐字稿时无法可靠判断口误和语义删除")
    return {"理解": goal, "证据": evidence, "假设": ["用户目标中的时间和风格描述真实有效"],
            "不确定性": uncertainty or ["无"], "备选方案": [],
            "选择理由": "规则模式只选择能从目标直接确定参数的技能，避免猜测时间区间。",
            "步骤": steps, "来源": "rules",
            "验收": "每一步的时长与结构变化都与预期一致",
            "停止条件": "缺少必要素材、草稿或无法从状态确定参数时停止并请求补充。"}


def plan_any(goal: str, state: dict, *, provider: str = "auto", force_rules: bool = False) -> dict:
    """既定制作请求直接路由；其他任务先试模型，失败时退回规则。"""
    production = state.get('production') or {}
    if force_rules or production.get('sheet') or production.get('request_id'):
        out = plan_rules(goal, state)
        return validate(out)[0]
    try:
        raw = llm.json_chat(build_messages(goal, state), provider=provider)
        raw["来源"] = "llm:" + (llm.CALL_LOG[-1]["provider"] if llm.CALL_LOG else "?")
        merged = dict(plan_rules(goal, state))
        merged.update({k: v for k, v in raw.items() if v})
        out, warnings = validate(merged)
        out["warnings"] = warnings
        if not out["步骤"]:
            raise ValueError("模型没给出可执行步骤")
        return out
    except Exception as exc:  # noqa: BLE001
        out = plan_rules(goal, state)
        out["fallback_reason"] = f"{type(exc).__name__}: {exc}"[:200]
        return validate(out)[0]


def knowledge_lines(state: dict) -> list[str]:
    """把"联网学到的做法"和"可执行配方"渲染成给模型看的几行。

    这是学习环真正起作用的地方：不写进提示词，学到的做法就永远只是躺在库里。
    """
    lines: list[str] = []
    for item in (state.get("knowledge") or [])[:8]:
        lines.append("【参考做法】" + str(item))
    recipes = state.get("recipes") or []
    if recipes:
        ready = [r for r in recipes if float(r.get("成功率") or 0) > 0]
        if ready:
            lines.append("【可用界面配方】" + "；".join(
                f"{r['id']}（{r['目标']}，历史成功率 {float(r['成功率']):.0%}）" for r in ready[:6]))
        else:
            pend = "；".join(f"{r['目标']}" for r in recipes[:4])
            lines.append(f"【未排练的界面配方】{pend}（要执行得先跑 skill_trial.py）")
        lines.append('（要用配方就选 ui_recipe 技能，args 填 {"recipe": "配方id"}）')
    return lines


def knowledge_lines(state: dict) -> list[str]:  # noqa: F811  覆盖上面的同名函数（含画面档案）
    """给模型看的"现状 + 参考做法 + 可执行配方"，画面档案放在最前面。

    顺序有讲究：先让模型知道"这段画面里有什么、哪个镜头能当钩子、哪里会挡字幕"，
    再给外部学到的做法与配方，最后才是偏好——这样它的取舍顺序和人一致。
    """
    lines: list[str] = []
    shots = state.get("shots") or {}
    if shots:
        hooks = shots.get("hook_candidates") or []
        if hooks:
            best = hooks[0]
            lines.append(f"【画面·钩子候选】镜头 {best.get('shot')}（{best.get('t')}s）"
                         f"可辨认度 {best.get('可辨认度')}、美学 {best.get('美学分')}、"
                         f"{str(best.get('主体', ''))[:14]}")
        if shots.get("weak_shots"):
            lines.append("【画面·别单独用】" + "、".join(
                f"#{w.get('shot')}（{w.get('原因')}）" for w in shots["weak_shots"][:4]))
        if shots.get("safe_area_conflicts"):
            lines.append("【画面·字幕冲突】" + "、".join(
                f"#{c.get('shot')}（{c.get('冲突')}）" for c in shots["safe_area_conflicts"][:4]))
        if shots.get("duplicate_groups"):
            lines.append("【画面·重复镜头】" + "、".join(
                "/".join(f"#{i}" for i in g) for g in shots["duplicate_groups"][:3]))
        for hint in (shots.get("hints") or [])[:5]:
            lines.append("【画面】" + str(hint))
    for item in (state.get("knowledge") or [])[:10]:
        lines.append("【参考做法】" + str(item))
    recipes = state.get("recipes") or []
    if recipes:
        ready = [r for r in recipes if float(r.get("成功率") or 0) > 0]
        if ready:
            lines.append("【可用界面配方】" + "；".join(
                f"{r['id']}（{r['目标']}，历史成功率 {float(r['成功率']):.0%}）" for r in ready[:6]))
        else:
            pend = "；".join(f"{r['目标']}" for r in recipes[:4])
            lines.append(f"【未排练的界面配方】{pend}（要执行得先跑 skill_trial.py）")
        lines.append('（要用配方就选 ui_recipe 技能，args 填 {"recipe": "配方id"}）')
    for item in (state.get("preferences") or [])[:8]:
        lines.append("【要遵守的偏好】" + str(item))
    return lines
