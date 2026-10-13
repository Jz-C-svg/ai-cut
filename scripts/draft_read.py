#!/usr/bin/env python3
r"""读草稿：把剪映工程读成统一 IR，打印它现在的真实状态。

主体的"眼睛"之一。任何决策前先读它，不做盲剪。

用法:
  python draft_read.py --list
  python draft_read.py --draft 月子知识-成片
  python draft_read.py --draft 月子知识-成片 --json        # 归一化 IR
  python draft_read.py --draft 月子知识-成片 --json --out work/ir.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="读剪映草稿 -> 统一 IR")
    ap.add_argument("--draft", help="草稿名或草稿目录/draft_content.json 路径")
    ap.add_argument("--list", action="store_true", help="列出 06-drafts 下的草稿")
    ap.add_argument("--json", action="store_true", help="输出归一化 IR（JSON）")
    ap.add_argument("--out", help="把结果写到文件")
    args = ap.parse_args()

    if args.list or not args.draft:
        drafts = cli.list_drafts()
        if not drafts:
            print("06-drafts 下没有草稿")
            return 1
        print(f"共 {len(drafts)} 个草稿：")
        for d in drafts:
            try:
                draft = Draft.load(d)
                print(f"  {d.name}  时长 {draft.declared_duration:.2f}s  片段 {len(list(draft.all_segments()))}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {d.name}  读取失败：{exc}")
        return 0

    draft = Draft.load(cli.resolve_draft(args.draft))

    if args.json:
        payload = draft.ir()
        if args.out:
            Path(args.out).write_text(
                __import__("json").dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"已写出 {args.out}")
        else:
            cli.print_json(payload)
        return 0

    lines = draft.describe()
    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已写出 {args.out}")
    cli.print_lines(lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
