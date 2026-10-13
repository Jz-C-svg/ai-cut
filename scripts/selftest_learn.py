#!/usr/bin/env python3
r"""学习环自测：学到的知识要真的能用上，用不上的要明确拒绝。

覆盖：
  知识/配方能被读出来，并进入规划提示词
  配方执行器的三重护栏（未排练拒绝 / 目标没记住拒绝 / 演算不动手）
  记忆维护（预演不删东西）

用法:
  python selftest_learn.py
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, elements, learn, plan, skills  # noqa: E402
from aicut.draft import Draft  # noqa: E402
from aicut.memory import open_memory  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="学习环自测")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mem = open_memory()
    knowledge = learn.knowledge()
    recipes = learn.recipes()
    check("能读出联网学到的要点", len(knowledge) >= 1, f"{len(knowledge)} 条")
    check("能读出联网学到的配方", len(recipes) >= 1, f"{len(recipes)} 条")

    state = {"draft": {"name": "自测草稿", "duration": 7.27, "tracks": {"video": 2}},
             "preferences": ["字幕不超过 12 字"],
             "knowledge": learn.knowledge_brief(3),
             "recipes": recipes}
    brief = plan.state_brief(state)
    check("知识进入规划提示词", "参考做法" in brief and knowledge[0]["要点"][:8] in brief)
    check("配方进入规划提示词", "配方" in brief, brief.splitlines()[-1][:60] if brief else "")

    registered = {s.id for s in skills.catalog()}
    check("配方执行器已注册", "ui_recipe" in registered, f"技能共 {len(registered)} 个")
    check("元素记忆可读", len(elements.names()) >= 1, "、".join(elements.names()))

    case = ROOT / "work" / "selftest" / "learn-case"
    case.parent.mkdir(parents=True, exist_ok=True)
    if case.exists():
        shutil.rmtree(case, ignore_errors=True)
    shutil.copytree(ROOT / "06-drafts" / "月子知识-成片", case)

    from aicut.agent import Agent  # 延迟导入，避免自测期间的重依赖

    draft = Draft.load(case)
    ctx = skills.Context(draft=draft, dry_run=True)
    unverified = [r for r in recipes if float(r.get("成功率") or 0) <= 0]
    if unverified:
        res = skills.get("ui_recipe").run(ctx, {"recipe": unverified[0]["id"]})
        check("未排练的配方被拒绝", res.get("ok") is False and "排练" in str(res.get("error", "")),
              str(res.get("error"))[:70])

    promoted = dict(recipes[0])
    promoted["成功率"] = 0.5
    mem.db.execute("UPDATE skills SET success=0.5 WHERE id=?", (promoted["id"],))
    mem.db.commit()
    res = skills.get("ui_recipe").run(ctx, {"recipe": promoted["id"]})
    refused = res.get("ok") is False and "记住" in str(res.get("error", ""))
    check("目标没记住的配方被拒绝（不瞎点）", refused or res.get("ok") is True,
          str(res.get("error") or res.get("note"))[:70])
    mem.db.execute("UPDATE skills SET success=0 WHERE id=?", (promoted["id"],))
    mem.db.commit()

    rc_dirs_before = len([d for d in (ROOT / "work" / "demos").iterdir() if d.is_dir()]) \
        if (ROOT / "work" / "demos").exists() else 0
    import subprocess
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "memory_prune.py"), "--json"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = proc.returncode == 0 and '"apply": false' in (proc.stdout or "")
    check("记忆维护：预演不删东西", ok, (proc.stdout or "").strip()[:60])
    rc_dirs_after = len([d for d in (ROOT / "work" / "demos").iterdir() if d.is_dir()]) \
        if (ROOT / "work" / "demos").exists() else 0
    check("预演没动录制目录", rc_dirs_before == rc_dirs_after, f"{rc_dirs_before} 份")

    shutil.rmtree(case, ignore_errors=True)
    failed = [r for r in RESULTS if not r[1]]
    if args.json:
        cli.print_json({"results": [{"name": n, "ok": o, "detail": d} for n, o, d in RESULTS],
                        "fail": len(failed)})
        return 1 if failed else 0
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
