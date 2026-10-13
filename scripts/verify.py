#!/usr/bin/env python3
r"""验证命令行：给两份草稿加一套断言，回答"这一步到底成没成"。

用法:
  python verify.py --before 旧草稿 --after 新草稿                 # 用默认规格
  python verify.py --before a --after b --spec spec.json          # 声明式规格
  python verify.py --before a --after b --duration-delta -2.05 -1.95 --shift -2.0
  python verify.py --visual 改前.png 改后.png --min-ratio 0.01     # 界面通道

规格文件示例:
  {"duration_delta": [-2.05, -1.95], "shift": -2.0, "no_overlap": true,
   "min_segment_len": {"video": 0.6}, "text_present": ["24小时"], "track_exists": ["video"]}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, verify  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()

DEFAULT_SPEC = {"no_overlap": True, "min_segment_len": {"video": 0.5}, "track_exists": ["video"]}


def main() -> int:
    ap = argparse.ArgumentParser(description="草稿断言")
    ap.add_argument("--before", help="改动前的草稿（做对比与时长的基准）")
    ap.add_argument("--after", required=False, help="改动后的草稿")
    ap.add_argument("--spec", help="规格 JSON 文件")
    ap.add_argument("--duration-delta", nargs=2, type=float, metavar=("LOW", "HIGH"))
    ap.add_argument("--shift", type=float, help="预期整体位移（秒）")
    ap.add_argument("--visual", nargs=2, metavar=("PNG_A", "PNG_B"), help="界面通道：比对两张截图")
    ap.add_argument("--min-ratio", type=float, default=0.002, help="界面变化的最小比例")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.visual:
        check = verify.check_screen_changed(args.visual[0], args.visual[1], args.min_ratio)
        report = verify.Report().add(check)
        if args.json:
            cli.print_json(report.to_dict())
        else:
            cli.print_lines(report.lines())
        return 0 if report.ok else 1

    if not args.after:
        raise SystemExit("需要 --after（或 --visual）")
    after = Draft.load(cli.resolve_draft(args.after))
    before = Draft.load(cli.resolve_draft(args.before)) if args.before else None
    spec = dict(DEFAULT_SPEC)
    if args.spec:
        spec.update(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    if args.duration_delta:
        spec["duration_delta"] = args.duration_delta
    if args.shift is not None:
        spec["shift"] = args.shift

    report = verify.from_spec(spec, before, after)
    if args.json:
        cli.print_json({**report.to_dict(), "spec": spec})
    else:
        print(f"草稿 {after.name} · 时长 {after.declared_duration:.3f}s · 片段 {len(list(after.all_segments()))}")
        cli.print_lines(report.lines())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
