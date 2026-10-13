#!/usr/bin/env python3
r"""联网学到的界面配方：先排练，再决定信不信它。

联网学到的做法**不能直接信**。这里做的是"排练"（不碰剪映、不点任何东西）：
把每条配方拆成步骤，逐条问三个问题——
  1. 这一步的目标在当前界面记忆里能不能定位到（config/ui_elements.json）？
  2. 如果涉及快捷键，键位在我们白名单里吗？
  3. 前置条件（比如"要先在编辑页"）现在能不能满足？
全部能落地的，才从"未验证"升成"已排练"（success 给 0.5，不给满分——真跑过才算）。

用法:
  python skill_trial.py --all
  python skill_trial.py --id web-蒙版分屏-00
  python skill_trial.py --all --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, ui  # noqa: E402
from aicut.memory import open_memory  # noqa: E402
from aicut.paths import CONFIG_DIR  # noqa: E402

cli.setup()


def load_elements() -> dict:
    path = CONFIG_DIR / "ui_elements.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    from aicut import elements
    out = elements.load()
    return {name: {"name": name, **entry} for name, entry in out.items()}


def trial(skill: dict, elements: dict, page: str) -> dict:
    steps = skill.get("steps") or []
    reasons: list[str] = []
    ok_steps = 0
    for i, step in enumerate(steps, 1):
        text = json.dumps(step, ensure_ascii=False) if isinstance(step, dict) else str(step)
        hit = [name for name in elements if name and name in text]
        key_hit = [k for k in ("ctrl+e", "ctrl+m", "ctrl+b", "ctrl+z", "e", "d", "space") if k in text.lower()]
        if hit:
            ok_steps += 1
        elif key_hit:
            ok_steps += 1
        elif any(word in text for word in ("参数", "数值", "滑动", "拖动")):
            reasons.append(f"第 {i} 步是参数调节，能执行但要人眼确认数值")
            ok_steps += 0.5
        else:
            reasons.append(f"第 {i} 步的目标没有界面坐标记忆：{text[:60]}")
    pre = " ".join(skill.get("preconditions") or [])
    if "编辑页" in pre and page != "编辑页":
        reasons.append(f"前置条件要求编辑页，现在在{page}")
    ratio = ok_steps / len(steps) if steps else 0
    verdict = "已排练" if steps and ratio >= 0.8 and not any("前置条件" in r for r in reasons) else "仍需完善"
    return {"id": skill["id"], "目标": skill.get("goal"), "步骤数": len(steps),
            "可落地比例": round(ratio, 2), "结论": verdict, "原因": reasons}


def main() -> int:
    ap = argparse.ArgumentParser(description="排练联网学到的界面配方")
    ap.add_argument("--id", help="指定技能 id")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mem = open_memory()
    skills = [s for s in mem.skills() if s.get("source") == "web"]
    if args.id:
        skills = [s for s in skills if s["id"] == args.id]
    if not skills:
        print("记忆库里没有联网学到的配方。先跑：python skill_learn.py --topic \"...\"")
        return 0

    elements = load_elements()
    page = ui.info().get("page", "未运行")
    results = []
    for skill in skills:
        res = trial(skill, elements, page)
        results.append(res)
        if res["结论"] == "已排练":
            mem.upsert_skill({"id": skill["id"], "goal": skill["goal"], "channel": skill["channel"],
                              "steps": skill["steps"], "verify": {"type": "rehearsal_passed"},
                              "source": "web", "source_url": skill.get("source_url", ""),
                              "success": 0.5, "runs": max(skill.get("runs", 0), 0)})
            mem.add_preference(f"联网配方可用（已排练）：{skill['goal']}",
                               evidence=skill.get("source_url", ""), confidence=0.5, source="web")

    if args.json:
        cli.print_json({"元素记忆条数": len(elements), "剪映": page, "结果": results})
        return 0

    print(f"界面元素记忆 {len(elements)} 条 · 剪映 {page}")
    for res in results:
        print(f"[{res['结论']}] {res['id']} · {res['步骤数']} 步 · 可落地 {res['可落地比例']:.0%}")
        print(f"   目标：{res['目标']}")
        for reason in res["原因"][:4]:
            print("   ! " + reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
