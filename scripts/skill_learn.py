#!/usr/bin/env python3
r"""联网学技能：检索公开资料 -> 整理成可操作做法与界面配方 -> 进记忆库。

学的是"做法"（怎么用蒙版分屏、参数取多少），不是别人的表达；每条都留来源链接。
界面配方会以 source=web、success=0（未验证）入库——只有在本机剪映里跑通、成功率
统计上来之后，才值得信任。

用法:
  python skill_learn.py --topic "蒙版分屏怎么做"
  python skill_learn.py --topic "关键帧做推近" --tokens 3000 --json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, learn  # noqa: E402
from aicut.memory import open_memory  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="联网学剪辑做法")
    ap.add_argument("--topic", required=True, help="要学什么，例如「蒙版分屏怎么做」")
    ap.add_argument("--tokens", type=int, default=2500)
    ap.add_argument("--provider", default="auto")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = learn.web_learn(args.topic, tokens=args.tokens, provider=args.provider)
    mem = open_memory()
    facts = mem.fact(f"web:{args.topic}") or {}

    if args.json:
        cli.print_json({**result, "要点": facts.get("要点")})
        return 0

    print(f"主题：{args.topic}")
    print(f"检索来源 {result['来源数']} 个，原文 {result['原文长度']} 字，"
          f"整理出要点 {result['要点数']} 条、界面配方 {result['配方数']} 条")
    for point in (facts.get("要点") or [])[:8]:
        print("  · " + str(point.get("要点", ""))[:100])
    for url in result["来源"][:5]:
        print("    来源：" + url)
    skills = [s for s in mem.skills() if s["source"] == "web"]
    if skills:
        print(f"记忆库里的联网技能：{len(skills)} 条（未验证，success=0）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
