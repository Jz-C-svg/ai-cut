#!/usr/bin/env python3
r"""跟你学：从你自己的历史成片里挖出剪辑习惯，写进记忆库当硬约束。

不需要你在场，也不需要剪映开着——读的是你自己的产物：
  03-scripts/*.sheet.json  你的剪辑决策单
  06-drafts/*/draft_content.json  真实落地的时间线

用法:
  python learn_from_demo.py                # 挖 + 写库 + 打印
  python learn_from_demo.py --dry          # 只看，不写库
  python learn_from_demo.py --report       # 生成 08-data/学习-我的剪辑习惯.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, learn  # noqa: E402
from aicut.paths import DATA  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="从历史成片学习你的剪辑习惯")
    ap.add_argument("--dry", action="store_true", help="只统计与提炼，不写进记忆库")
    ap.add_argument("--report", action="store_true", help="额外生成 markdown 报告")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    stats = learn.mine_from_workspace()
    rules = learn.preferences_from(stats)

    if not args.dry:
        learn.apply_preferences(stats=stats)

    if args.json:
        cli.print_json({"stats": stats, "rules": rules, "written": not args.dry})
    else:
        print(f"样本：{stats['草稿数']} 个草稿、{stats['决策单数']} 份决策单")
        print(f"你的镜头中位时长：{stats['镜头']['中位时长']}s　"
              f"字幕中位字数：{stats['字幕']['中位字数']}　封面中位字数：{stats['封面字数']['中位']}")
        print("提炼出的偏好：" if not args.dry else "提炼出的偏好（未写库）：")
        for item in rules:
            print(f"  {item['confidence']:.0%}  {item['rule']}")
        print(f"\n共 {len(rules)} 条" + ("" if args.dry else "，已写进 memory.sqlite 的 preferences 表"))

    if args.report:
        out = DATA / "学习-我的剪辑习惯.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(learn.report_markdown(stats=stats, rules=rules), encoding="utf-8")
        print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
