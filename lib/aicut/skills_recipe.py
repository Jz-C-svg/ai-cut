r"""配方执行器：把"联网学到的界面做法"真正跑出来。

这是学习环最后一段：知识 → 配方 → 执行 → 记账（成功率）。三条护栏：
  1. 只有 skill_trial 排练过（success>0）的配方才允许执行，否则明确拒绝；
  2. 每一步都要能定位：能在元素记忆里找到目标就点它，遇到白名单键位就按键，
     两者都不是就**停下并列出"要教它记住什么"**，绝不瞎点;
  3. --dry-run 时只演算，不动手。

执行完会把这次成败记到配方自己的成功率上（memory.record_run），跑过几次、成过几次
一目了然——这就是"越用越准"的那部分。
"""
from __future__ import annotations

from . import elements, messages, skills, ui
from .memory import open_memory

KEY_WORDS = ("ctrl+", "alt+", "shift+", "快捷键", "按 ")


def _step_text(step) -> str:
    if isinstance(step, dict):
        parts = [str(v) for v in step.values() if v]
        return " ".join(parts)
    return str(step)


def _plan_step(step) -> dict:
    """决定这一步是点击（用元素记忆）还是按键。"""
    text = _step_text(step)
    name = elements.locate(text)
    if name:
        return {"方式": "点击", "元素": name, "原文": text[:80]}
    lowered = text.lower()
    for word in KEY_WORDS:
        if word in lowered:
            return {"方式": "按键", "键位": word.strip(), "原文": text[:80], "待确认": True}
    return {"方式": "无法定位", "原文": text[:80]}


def skill_ui_recipe(ctx: skills.Context, args: dict) -> dict:
    recipe_id = str(args.get("recipe") or "")
    mem = open_memory()
    skill = mem.get_skill(recipe_id)
    if not skill:
        return {"op": "ui_recipe", "ok": False, "error": f"记忆库里没有配方 {recipe_id}"}
    steps = skill.get("steps") or []
    if not steps:
        return {"op": "ui_recipe", "ok": False, "error": f"配方 {recipe_id} 没有可执行步骤"}
    verified = float(skill.get("success") or 0) > 0
    if not verified:
        return {"op": "ui_recipe", "ok": False,
                "error": f"配方 {recipe_id} 还没排练过（success=0）：先跑 skill_trial.py --id {recipe_id}"}

    plan = [_plan_step(step) for step in steps]
    unknown = [p for p in plan if p["方式"] == "无法定位"]
    if unknown:
        return {"op": "ui_recipe", "ok": False, "plan": plan,
                "error": ("有步骤的目标没记住，先教它一次再跑："
                          + "、".join(p["原文"][:40] for p in unknown[:3]))
                + ("（已记住的元素：" + "、".join(elements.names() or ["无"]) + "）")}

    if ctx.dry_run:
        return {"op": "ui_recipe", "ok": True, "plan": plan,
                "note": messages.DRY_SHELL_SKIP if False else "演算：配方可执行，未动手"}

    done: list[dict] = []
    failed = 0
    for item in plan:
        try:
            if item["方式"] == "点击":
                pid = ui.require()
                info = ui.info(pid)
                rect = tuple(info["rect"])
                x, y = elements.resolve(item["元素"], rect)
                left, top, _, _ = rect
                res = ui.click(x - left, y - top, pid=pid)
                elements.touch(item["元素"], ok=True)
                done.append({"点击": item["元素"], "cost_ms": res["cost_ms"]})
            else:
                key = item.get("键位", "")
                res = ui.key(key)
                done.append({"按键": key, "cost_ms": res["cost_ms"]})
        except Exception as exc:  # noqa: BLE001
            failed += 1
            done.append({"失败": item.get("元素") or item.get("键位") or item["方式"],
                         "原因": f"{type(exc).__name__}: {exc}"[:120]})

    ok = failed == 0
    mem.record_run(recipe_id, ok)
    return {"op": "ui_recipe", "ok": ok, "recipe": recipe_id, "执行": done, "失败步数": failed,
            "note": f"{skill.get('goal') or recipe_id}：{len(done) - failed}/{len(done)} 步成功"}


skills.register(skills.Skill(
    id="ui_recipe",
    goal="执行一条已排练过的联网界面配方（按元素记忆点击 / 按白名单键位按键）",
    channel="ui",
    args={"recipe": "配方 id（见 memory_cli.py --skills）"},
    sample={"recipe": "web-剪映-蒙版分屏怎么做-00"},
    required=["recipe"],
    fn=skill_ui_recipe,
    notes="只有 success>0（排练过）的配方才允许执行；无法定位的步骤会拒绝并提示要教什么",
))
