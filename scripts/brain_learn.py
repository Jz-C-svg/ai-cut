#!/usr/bin/env python3
"""主脑联网学习入口。

示例：
  python brain_learn.py --topic "视频智能体的事件驱动架构"
  python brain_learn.py --topic "字幕安全区" --no-recipes --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut.brain_learning import BrainLearner  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="AI cut 主脑联网学习")
    ap.add_argument("--topic", action="append", required=True, help="学习主题，可重复")
    ap.add_argument("--question", help="自定义检索问题（单主题时生效）")
    ap.add_argument("--provider", default="auto", choices=["auto", "deepseek", "ollama"])
    ap.add_argument("--tokens", type=int, default=3000)
    ap.add_argument("--no-recipes", action="store_true", help="不提炼可执行界面配方")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    learner = BrainLearner(provider=args.provider)
    results = []
    for topic in args.topic:
        result = learner.learn(
            topic, question=args.question if len(args.topic) == 1 else "",
            want_recipes=not args.no_recipes, tokens=args.tokens)
        results.append(result.as_dict())
        if not args.json:
            print(f"[主脑学习] {result.topic}：要点 {result.points}，"
                  f"配方 {result.recipes}，来源 {len(result.sources)}")
            for source in result.sources[:6]:
                print(f"  - {source}")
            if result.error:
                print(f"  错误：{result.error}")

    if args.json:
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if all(r["points"] > 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
