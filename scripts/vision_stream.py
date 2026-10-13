#!/usr/bin/env python3
r"""画面实时识别的落地形态：素材视觉事件流。

全片 2fps 抽帧算指标（清晰度/曝光/运动/饱和度/肤色占比），按镜头聚合，给出
"哪段糊、哪段过曝、哪个镜头主体最清晰、哪段运动大"——这些是剪辑能直接用的信号。
结果按素材指纹缓存进 memory.sqlite，二次调用直接返回。

用法:
  python vision_stream.py --video "work\normalized\月子知识-0310.mp4"
  python vision_stream.py --video x.mp4 --json --out work/vision-x.json
  python vision_stream.py --video x.mp4 --no-cache --fps 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, vision  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="素材视觉事件流")
    ap.add_argument("--video", required=True, help="素材路径")
    ap.add_argument("--fps", type=float, default=vision.DEFAULT_FPS)
    ap.add_argument("--no-cache", action="store_true", help="忽略缓存重新算")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--out", help="把 JSON 写到文件")
    args = ap.parse_args()

    stream = vision.analyze(args.video, fps=args.fps, use_cache=not args.no_cache)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(stream, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        payload = dict(stream)
        if not args.out:
            payload.pop("frames", None)
        cli.print_json(payload)
        return 0

    cli.print_lines(vision.describe(stream))
    if args.out:
        print(f"完整结果已写出：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
