#!/usr/bin/env python3
r"""记忆库维护：长期跑下去必须能自我瘦身。

做四件事（都可单独开关）：
  --episodes-keep N   每个任务只留最近 N 步（默认 200；超出的删）
  --snapshots-keep N  每个草稿只留最近 N 个快照（连带删除磁盘目录）
  --recordings-keep N work/demos 录制证据只留最近 N 份
  --vacuum            压缩数据库文件

默认只报告（--dry-run 行为），加 --apply 才真删。

用法:
  python memory_prune.py                       # 看看能瘦多少
  python memory_prune.py --apply
  python memory_prune.py --episodes-keep 100 --snapshots-keep 5 --recordings-keep 30 --apply
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, learn  # noqa: E402
from aicut.memory import open_memory  # noqa: E402
from aicut.paths import WORK  # noqa: E402
from aicut.snapshot import prune as prune_snapshots  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="记忆库瘦身")
    ap.add_argument("--episodes-keep", type=int, default=200, help="每个任务保留多少步")
    ap.add_argument("--snapshots-keep", type=int, default=8, help="每个草稿保留多少快照")
    ap.add_argument("--recordings-keep", type=int, default=40, help="work/demos 保留多少份")
    ap.add_argument("--apply", action="store_true", help="真删（默认只看）")
    ap.add_argument("--vacuum", action="store_true", help="压缩数据库")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mem = open_memory()
    db = Path(mem.path)
    before = db.stat().st_size if db.exists() else 0
    report: dict = {"apply": args.apply, "db_before_mb": round(before / 1024 / 1024, 2)}

    rows = mem.db.execute(
        "SELECT task, COUNT(*) n FROM episodes GROUP BY task HAVING n > ?", (args.episodes_keep,)
    ).fetchall()
    victims = 0
    for row in rows:
        extra = int(row["n"]) - args.episodes_keep
        victims += extra
        if args.apply:
            mem.db.execute(
                "DELETE FROM episodes WHERE task=? AND id NOT IN"
                " (SELECT id FROM episodes WHERE task=? ORDER BY id DESC LIMIT ?)",
                (row["task"], row["task"], args.episodes_keep),
            )
    report["episodes_to_trim"] = victims

    drafts = {r["draft"] for r in mem.db.execute("SELECT DISTINCT draft FROM snapshots")}
    snap_removed = 0
    for draft in drafts:
        count = mem.db.execute("SELECT COUNT(*) c FROM snapshots WHERE draft=?", (draft,)).fetchone()["c"]
        if count <= args.snapshots_keep:
            continue
        snap_removed += count - args.snapshots_keep
        if args.apply:
            prune_snapshots(draft, keep=args.snapshots_keep, memory=mem)
            old = mem.db.execute(
                "SELECT id FROM snapshots WHERE draft=? ORDER BY created DESC LIMIT -1 OFFSET ?",
                (draft, args.snapshots_keep),
            ).fetchall()
            for r in old:
                mem.db.execute("DELETE FROM snapshots WHERE id=?", (r["id"],))
    report["snapshots_to_remove"] = snap_removed

    demos = WORK / "demos"
    demo_dirs = sorted([d for d in demos.iterdir() if d.is_dir()]) if demos.exists() else []
    report["recordings_total"] = len(demo_dirs)
    report["recordings_to_remove"] = max(0, len(demo_dirs) - args.recordings_keep)
    if args.apply and report["recordings_to_remove"]:
        report["recordings_pruned"] = learn.prune_recordings(args.recordings_keep)

    if args.apply:
        mem.db.commit()
        if args.vacuum:
            mem.db.execute("VACUUM")
        after = db.stat().st_size if db.exists() else 0
        report["db_after_mb"] = round(after / 1024 / 1024, 2)
    report["stats"] = mem.stats()

    if args.json:
        cli.print_json(report)
        return 0
    print(f"数据库 {report['db_before_mb']} MB · "
          f"episodes {report['stats']['episodes']} · snapshots {report['stats']['snapshots']} · "
          f"skills {report['stats']['skills']} · preferences {report['stats']['preferences']}")
    print(f"可瘦身：任务步数 {report['episodes_to_trim']} 条、快照 {report['snapshots_to_remove']} 个、"
          f"录制证据 {report['recordings_to_remove']}/{report['recordings_total']} 份")
    if args.apply:
        print(f"已执行。数据库现在 {report.get('db_after_mb')} MB")
    else:
        print("（这是预演；加 --apply 才真删）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
