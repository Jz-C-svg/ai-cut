#!/usr/bin/env python3
r"""v2 主体自测：规划、守卫、执行、验证、记账、学习。

全部在 work/selftest 里做副本，默认不调用云端模型（用规则规划，结果可复现）。
上模型的那一条用 --llm 打开。

用法:
  python selftest_agent.py
  python selftest_agent.py --llm
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, guard, learn, plan as plan_mod, skills  # noqa: E402
from aicut import snapshot as snap_mod  # noqa: E402
from aicut.agent import Agent, summarize_report  # noqa: E402
from aicut.draft import Draft  # noqa: E402
from aicut.memory import open_memory  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="v2 主体自测")
    ap.add_argument("--draft", default=None)
    ap.add_argument("--llm", action="store_true", help="额外跑一条真实模型规划")
    args = ap.parse_args()

    if args.draft:
        ref = cli.resolve_draft(args.draft)
    else:
        candidates = sorted(
            p for p in (ROOT / "06-drafts").iterdir()
            if p.is_dir() and (p / "draft_content.json").exists()
        )
        if not candidates:
            raise SystemExit("06-drafts 中没有可用于主体自测的草稿")
        ref = candidates[0]
    case = ROOT / "work" / "selftest" / "agent-case"
    shutil.rmtree(case, ignore_errors=True)
    shutil.copytree(ref, case)
    base = Draft.load(ref)
    quiet = lambda *_: None  # noqa: E731

    cat = skills.catalog()
    check("技能表已注册", len(cat) >= 8, f"{len(cat)} 个：" + "、".join(s.id for s in cat))
    check("每个技能都有目标和通道", all(s.goal and s.channel for s in cat))
    check("工程通道技能有验收规格", all(s.verify for s in cat if s.channel == "draft"))

    for goal, expect in [
        ("删掉开头 3 秒", "delete_head"),
        ("剪掉所有停顿", "delete_silences"),
        ("把它压到 20 秒", "trim_to"),
        ("删掉 2 秒到 5 秒", "delete_range"),
        ("按卡点出片", "generate_draft"),
    ]:
        plan = plan_mod.validate(plan_mod.plan_rules(goal, {"draft": {"name": "x", "duration": 30}}))[0]
        got = [s["skill"] for s in plan["步骤"]]
        check(f"规则规划「{goal}」", expect in got, "、".join(got) or "无")

    plan = plan_mod.plan_rules("剪掉停顿再按口播出片", {"draft": None})
    kept, dropped = guard.filter_steps(plan["步骤"], {"draft": None})
    check("守卫：无草稿时摘掉工程通道步骤",
          bool(dropped) and all(s["skill"] != "delete_silences" for s in kept),
          f"保留 {[s['skill'] for s in kept]}，摘掉 {len(dropped)}")

    agent = Agent(draft=case, goal="剪掉所有停顿", use_rules=True, use_vision=False, dry_run=True, log=quiet)
    result = agent.run()
    check("主体：演算跑通且有步骤", result["ok"] and result["steps"], f"{len(result['steps'])} 步")
    check("主体：时长确实变短", result["duration"]["end"] < base.declared_duration,
          f"{result['duration']['start']:.2f} -> {result['duration']['end']:.2f}s")
    check("主体：每步都带验证结果", all(s["checks"] for s in result["steps"]))
    check("主体：演算没有改动磁盘",
          (case / "draft_content.json").read_bytes() == (ref / "draft_content.json").read_bytes())

    agent2 = Agent(draft=case, goal="删掉开头 2 秒", use_rules=True, use_vision=False, log=quiet)
    result2 = agent2.run()
    check("主体：真剪成功且落盘",
          result2["ok"] and Draft.load(case).declared_duration < base.declared_duration,
          f"{base.declared_duration:.2f} -> {Draft.load(case).declared_duration:.2f}s")
    snap = result2["steps"][0]["snapshot"]
    check("主体：每步都先留了快照", bool(snap), snap)
    snap_mod.restore(snap, backup=False)
    check("回滚：回到原始时长", abs(Draft.load(case).declared_duration - base.declared_duration) < 0.01)

    mem = open_memory()
    kinds = {r["kind"] for r in mem.episodes(result2["task"])}
    check("记忆库：感知-规划-步骤-结果都记账",
          {"perceive", "plan", "step", "result"} <= kinds, "、".join(sorted(kinds)))
    check("记忆库：能查到任务列表", any(t["task"] == result2["task"] for t in mem.tasks(limit=50)))

    stats = learn.mine_from_workspace()
    rules = learn.preferences_from(stats)
    check("学习：从历史草稿挖出统计", stats["草稿数"] >= 1 and stats["镜头"]["样本"] >= 1,
          f"{stats['草稿数']} 个草稿、{stats['镜头']['样本']} 个片段")
    check("学习：提炼出可执行偏好", len(rules) >= 3, f"{len(rules)} 条")
    check("学习：偏好已进记忆库", len(mem.preferences()) >= 1)

    text = summarize_report({"op": "generate_draft", "ok": False, "rc": 2,
                             "stdout_tail": "质量门检查：\n  ! 缺少 cover_text\n已停止。"})
    check("失败可读：出片失败会带原因", "rc=2" in text and "cover_text" in text, text[:70])
    text2 = summarize_report({"op": "delete_silences", "removed": 0, "applied": []})
    check("失败可读：空操作会说明没命中", "没" in text2, text2[:50])

    if args.llm:
        plan = plan_mod.plan_any("删掉开头两秒再剪掉停顿",
                                 {"draft": {"name": "x", "duration": 20},
                                  "styles": plan_mod.available_styles()})
        check("模型规划可用", bool(plan["步骤"]),
              f"来源 {plan.get('来源')}，步骤 {[s['skill'] for s in plan['步骤']]}")

    shutil.rmtree(case, ignore_errors=True)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
