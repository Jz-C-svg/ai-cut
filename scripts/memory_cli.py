#!/usr/bin/env python3
r"""记忆库命令行：看主体记住了什么，也能手动补一条。

用法:
  python memory_cli.py --stats
  python memory_cli.py --tasks
  python memory_cli.py --episodes <task>
  python memory_cli.py --skills
  python memory_cli.py --prefs
  python memory_cli.py --add-pref "钩子永远提到最前" --evidence "手工剪了 3 条都这么做"
  python memory_cli.py --facts
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli  # noqa: E402
from aicut.memory import open_memory  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="memory.sqlite 记忆库")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--tasks", action="store_true")
    ap.add_argument("--episodes", metavar="TASK")
    ap.add_argument("--skills", action="store_true")
    ap.add_argument("--prefs", action="store_true")
    ap.add_argument("--facts", action="store_true")
    ap.add_argument("--add-pref", metavar="RULE")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--confidence", type=float, default=0.7)
    ap.add_argument("--source", default="manual")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mem = open_memory()

    if args.add_pref:
        mem.add_preference(args.add_pref, evidence=args.evidence, confidence=args.confidence, source=args.source)
        print(f"已记住偏好：{args.add_pref}")
        return 0

    if args.tasks:
        rows = mem.tasks()
        if args.json:
            cli.print_json(rows)
            return 0
        for r in rows:
            print(f"{r['task']}  {r['n']} 步  失败 {r['fails'] or 0}")
        return 0

    if args.episodes:
        rows = mem.episodes(args.episodes)
        if args.json:
            cli.print_json(rows)
            return 0
        for r in reversed(rows):
            mark = "" if r["ok"] is None else ("OK " if r["ok"] else "FAIL ")
            print(f"[{r['step']:>2}] {r['kind']:<10} {mark}{r['goal']}")
        return 0

    if args.skills:
        rows = mem.skills()
        if args.json:
            cli.print_json(rows)
            return 0
        for r in rows:
            print(f"{r['id']:<28} {r['channel']:<7} 成功 {r['success']:.0%} ({r['runs']} 次) {r['goal']}")
        return 0

    if args.prefs:
        rows = mem.preferences()
        if args.json:
            cli.print_json(rows)
            return 0
        for r in rows:
            print(f"{r['confidence']:.0%}  {r['rule']}   （{r['source']}）")
        return 0

    if args.facts:
        cli.print_json(mem.facts())
        return 0

    cli.print_json(mem.stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
