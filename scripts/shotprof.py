#!/usr/bin/env python3
r"""镜头视觉档案：让 AI 真正"看得懂"每条素材的每个镜头。

输出两样东西：
  指标（便宜档，来自 vision.py）  清晰度/曝光/运动/肤色
  理解（精细档，本模块）          主体/动作/景别/构图/光线/可辨认度/适不适合做钩子/
                                  字幕会不会挡脸/有没有压字水印/美学分 + 本地算出的
                                  主体框、安全区重叠、抖动、闪烁、重复镜头

用法:
  python shotprof.py --video "work\normalized\月子知识-0299.mp4"
  python shotprof.py --video x.mp4 --json --out work/shotprof.json
  python shotprof.py --video x.mp4 --no-cloud --max-shots 4   # 只用本地视觉模型/限量
  python shotprof.py --video x.mp4 --force                    # 忽略缓存重算
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, shotprof  # noqa: E402

cli.setup()


def main() -> int:
    ap = argparse.ArgumentParser(description="镜头视觉档案")
    ap.add_argument("--video", required=True, help="素材路径")
    ap.add_argument("--max-shots", type=int, help="最多给几个镜头做语义理解（省调用）")
    ap.add_argument("--no-cloud", action="store_true", help="只用本地视觉模型")
    ap.add_argument("--force", action="store_true", help="忽略缓存重算")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", help="把完整档案写到文件")
    args = ap.parse_args()

    profile = shotprof.build(args.video, max_shots=args.max_shots,
                            use_cloud=not args.no_cloud, force=args.force)
    if args.out:
        Path(args.out).write_text(__import__("json").dumps(profile, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        print(f"完整档案：{args.out}")
    if args.json:
        payload = dict(profile)
        payload["shots"] = payload.get("shots", [])
        cli.print_json(payload)
        return 0
    cli.print_lines(shotprof.describe(profile))
    if profile.get("file"):
        print(f"档案文件：{profile['file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
