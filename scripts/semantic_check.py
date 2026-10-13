#!/usr/bin/env python3
r"""语义断言：让本地视觉模型看一眼画面，回答一个 YES/NO 问题。

结构断言能证明"时间线对了"，证明不了"画面对了"。这个脚本补上那一段：
  出片后首帧里有没有人物？
  这一段是不是糊的（构图是否可辨）？
  画面里有没有压字/水印？

用法:
  python semantic_check.py --video "07-output\x.mp4" --at 0.3 --ask "这一帧里能看到人物吗"
  python semantic_check.py --video x.mp4 --at 1.0 --ask "画面清晰可辨吗" --json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, verify  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="语义断言（本地视觉模型）")
    ap.add_argument("--video", required=True)
    ap.add_argument("--at", type=float, default=0.3, help="取第几秒的帧")
    ap.add_argument("--ask", required=True, help="YES/NO 问题")
    ap.add_argument("--out", help="把取到的帧存到哪")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    check = verify.check_semantic(args.video, args.at, args.ask, out=args.out)
    if args.json:
        cli.print_json(check.__dict__)
    else:
        cli.print_lines([check.line()])
        if check.detail:
            print(f"帧：{check.detail}")
    return 0 if check.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
