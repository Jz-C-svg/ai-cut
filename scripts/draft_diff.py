#!/usr/bin/env python3
r"""比较两份草稿：这次操作到底改了什么。

用法:
  python draft_diff.py --a 月子知识-成片 --b 月子知识-成片-v2
  python draft_diff.py --a a.json --b b.json --json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, diff as diff_mod  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="草稿 IR 差异")
    ap.add_argument("--a", required=True, help="改动前的草稿")
    ap.add_argument("--b", required=True, help="改动后的草稿")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    a = Draft.load(cli.resolve_draft(args.a))
    b = Draft.load(cli.resolve_draft(args.b))
    result = diff_mod.diff(a, b)

    if args.json:
        cli.print_json(result)
        return 0

    cli.print_lines(result["summary"])
    if result["uniform_shift"]:
        u = result["uniform_shift"]
        print(f"（{u['count']} 个片段位移 {u['delta']:+.3f}s）")
    for c in result["changed"][:20]:
        print(f"  {c['track']} {c['id'][:8]} {c['field']}: {c['before']} -> {c['after']}")
    if len(result["changed"]) > 20:
        print(f"  … 另有 {len(result['changed']) - 20} 处字段变化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
