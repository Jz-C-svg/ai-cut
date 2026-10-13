#!/usr/bin/env python3
r"""草稿快照：拍一张、列出来、滚回去。

剪映没有"保存"快捷键，编辑靠自动保存。主体在动手术前必须先留后路，所以快照是
每次操作的默认动作。

用法:
  python draft_snapshot.py --do 月子知识-成片 --note "改之前"
  python draft_snapshot.py --list 月子知识-成片
  python draft_snapshot.py --restore 20260911-101010-123
  python draft_snapshot.py --prune 月子知识-成片 --keep 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, snapshot as snap_mod  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="草稿快照与回滚")
    ap.add_argument("--do", metavar="DRAFT", help="给这个草稿拍快照")
    ap.add_argument("--note", default="", help="快照备注")
    ap.add_argument("--list", metavar="DRAFT", nargs="?", const="", help="列出快照")
    ap.add_argument("--restore", metavar="SNAPSHOT_ID", help="回滚到某个快照")
    ap.add_argument("--prune", metavar="DRAFT", help="只保留最近 N 个快照")
    ap.add_argument("--keep", type=int, default=20)
    ap.add_argument("--no-backup", action="store_true", help="回滚前不再自动备份当前状态")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.do:
        info = snap_mod.snapshot(cli.resolve_draft(args.do), note=args.note)
        cli.print_json(info) if args.json else print(
            f"快照 {info['id']} · {info['draft']} · {info['duration']:.2f}s · {info['path']}"
        )
        return 0

    if args.list is not None:
        rows = snap_mod.snapshots(args.list or None)
        if args.json:
            cli.print_json(rows)
            return 0
        if not rows:
            print("没有快照")
            return 0
        for r in rows:
            import time as _t
            when = _t.strftime("%m-%d %H:%M", _t.localtime(r["created"]))
            print(f"{r['id']}  {when}  {r['draft']}  {r['note']}")
        return 0

    if args.restore:
        info = snap_mod.restore(args.restore, backup=not args.no_backup)
        cli.print_json(info) if args.json else print(
            f"已回滚 {info['snapshot']} -> {info['draft']}（{info['duration']:.2f}s）"
        )
        return 0

    if args.prune:
        removed = snap_mod.prune(args.prune, keep=args.keep)
        print(f"清理 {len(removed)} 个旧快照")
        return 0

    raise SystemExit("用法见 --help")


if __name__ == "__main__":
    raise SystemExit(main())
