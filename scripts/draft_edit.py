#!/usr/bin/env python3
r"""改草稿：工程通道的编辑原语，带快照、diff 与验证。

这是"动手"的第一条通道：直接改 draft_content.json，精确、可回滚、可验证。
默认会先拍快照，再改，再 diff；--dry-run 只演算不落盘。

用法:
  python draft_edit.py --draft 月子知识-成片 --delete 0 2.0 --dry-run
  python draft_edit.py --draft 月子知识-成片 --delete 0 2.0
  python draft_edit.py --draft 月子知识-成片 --delete 0.9 2.8 --out 月子知识-成片-v2
  python draft_edit.py --draft 月子知识-成片 --shift 0.5
  python draft_edit.py --draft 月子知识-成片 --speed <片段id> 1.5
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, diff as diff_mod, edit, snapshot as snap_mod, verify  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()


def _find_segment(draft: Draft, seg_id: str):
    for seg in draft.all_segments():
        if seg.id == seg_id or seg.id.startswith(seg_id):
            return seg
    raise SystemExit(f"找不到片段 {seg_id}")


def main() -> int:
    ap = argparse.ArgumentParser(description="工程通道草稿编辑（带快照与验证）")
    ap.add_argument("--draft", required=True, help="草稿名或路径")
    ap.add_argument("--delete", nargs=2, type=float, metavar=("T0", "T1"), help="ripple 删除时间线区间")
    ap.add_argument("--shift", type=float, help="整条时间线平移（秒）")
    ap.add_argument("--speed", nargs=2, metavar=("SEG", "V"), help="给片段的 id 设变速")
    ap.add_argument("--out", help="另存为 06-drafts 下的新草稿名（不改原件）")
    ap.add_argument("--dry-run", action="store_true", help="只演算，不落盘")
    ap.add_argument("--no-snapshot", action="store_true", help="跳过快照（不建议）")
    ap.add_argument("--spec", help="验证规格 json 文件")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args()

    src = cli.resolve_draft(args.draft)
    before_draft = Draft.load(src)
    out_dir: Path | None = None
    if args.out:
        out_dir = ROOT / "06-drafts" / args.out
        if out_dir.exists():
            raise SystemExit(f"目标草稿已存在：{out_dir}")
        shutil.copytree(src, out_dir)

    target_dir = out_dir or src
    snapshot_info = None
    if not args.dry_run and not args.no_snapshot and not out_dir:
        snapshot_info = snap_mod.snapshot(target_dir, note=f"before {args.delete or args.shift or args.speed}")

    draft = Draft.load(target_dir)
    reports = []

    if args.delete:
        t0, t1 = args.delete
        reports.append(edit.ripple_delete(draft, t0, t1, dry_run=args.dry_run))
    if args.shift:
        edit.shift_all(draft, args.shift)
        reports.append({"op": "shift_all", "delta": args.shift})
    if args.speed:
        seg = _find_segment(draft, args.speed[0])
        edit.set_speed(seg, float(args.speed[1]))
        reports.append({"op": "set_speed", "id": seg.id, "value": float(args.speed[1])})
    if not reports:
        raise SystemExit("没有指定任何操作（--delete / --shift / --speed）")

    if not args.dry_run:
        draft.save()
        draft.reload()

    after_draft = draft if not args.dry_run else draft
    result = diff_mod.diff(before_draft, after_draft)
    verify_rep = None
    if args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        verify_rep = verify.from_spec(spec, before_draft, after_draft)

    if args.json:
        cli.print_json({
            "draft": target_dir.name,
            "dry_run": args.dry_run,
            "snapshot": snapshot_info,
            "ops": reports,
            "diff": {k: v for k, v in result.items() if k != "changed"},
            "verify": verify_rep.to_dict() if verify_rep else None,
        })
    else:
        print(f"草稿 {target_dir.name} · {'演算（未落盘）' if args.dry_run else '已落盘'}")
        if snapshot_info:
            print(f"快照：{snapshot_info['id']}")
        for rep in reports:
            if rep.get("op") == "ripple_delete":
                print(f"删除 [{rep['range'][0]:.2f}, {rep['range'][1]:.2f}]s · "
                      f"整段删除 {len(rep['deleted'])} · 掐头尾 {len(rep['trimmed'])} · "
                      f"切割 {len(rep['split'])} · 前移 {rep['shifted']}")
        print("变化：")
        cli.print_lines("  " + line for line in result["summary"])
        if verify_rep:
            cli.print_lines(verify_rep.lines())

    if verify_rep and not verify_rep.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
