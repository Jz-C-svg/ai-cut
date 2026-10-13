#!/usr/bin/env python3
r"""看主体在干什么、干过什么（文字版"直播"）。

数据全来自 memory.sqlite，不依赖进程是否在跑：
  episodes 每一步的感知-决策-动作-结果
  snapshots 快照与回滚记录

用法:
  python agent_status.py                  # 最近任务
  python agent_status.py --task 0911-104833-material
  python agent_status.py --tail 40        # 看更长的日志
  python agent_status.py --json
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli  # noqa: E402
from aicut.memory import open_memory  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="主体任务状态")
    ap.add_argument("--task", help="指定任务 id")
    ap.add_argument("--tail", type=int, default=20, help="最多显示多少条")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mem = open_memory()
    task = args.task
    if not task:
        tasks = mem.tasks(limit=1)
        if not tasks:
            print("还没有任何任务记录。先跑一次：python agent.py --draft X --goal \"...\"")
            return 0
        task = tasks[0]["task"]

    rows = mem.episodes(task, limit=args.tail)
    if args.json:
        cli.print_json({"task": task, "episodes": rows, "snapshots": mem.snapshots()})
        return 0

    print(f"任务 {task}")
    for row in reversed(rows):
        when = time.strftime("%H:%M:%S", time.localtime(row["created"]))
        mark = "" if row["ok"] is None else ("[OK]  " if row["ok"] else "[FAIL]")
        detail = row.get("detail") or {}
        note = ""
        if isinstance(detail, dict):
            note = detail.get("note") or detail.get("understanding") or ""
            if not note and detail.get("report"):
                note = str(detail["report"])[:80]
            if not note and detail.get("duration"):
                note = f"{detail['duration'][0]} -> {detail['duration'][1]}s"
        print(f"{when} {mark} {row['kind']:<8} {row['goal'] or ''}  {str(note)[:90]}")
    shots = mem.snapshots(limit=5)
    if shots:
        print("\n最近快照：")
        for s in shots:
            print(f"  {s['id']}  {s['draft']}  {s['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
