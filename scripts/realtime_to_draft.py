#!/usr/bin/env python3
r"""实时打点 -> 草稿 一条命令打通：实时看 -> 决策 -> 改草稿。

链路：
  ai-auto-editor program watch 实时识别镜头
    -> data/program_edits/打点_*.json
    -> realtime_bridge.load_marks() 读取
    -> 技能 apply_realtime_marks 执行 split（+ 可选低分删除）
    -> 草稿保存 + 断言验证

用法:
  # 只看计划，不改草稿
  python realtime_to_draft.py --draft 月子知识-成片45 --marks <打点.json> --dry-run

  # 真切分
  python realtime_to_draft.py --draft 月子知识-成片45 --marks <打点.json>

  # 切分 + 删掉低分镜头
  python realtime_to_draft.py --draft 月子知识-成片45 --marks <打点.json> --delete-low

  # 不传 --marks 时自动取 program_edits 目录里最新的一份
  python realtime_to_draft.py --draft 月子知识-成片45 --latest
"""
from __future__ import annotations
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, realtime_bridge, skills, verify  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()

# ai-auto-editor 打点输出目录（只读消费，不改它）
PROGRAM_EDITS = (Path(portable_path('user:AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/.user_skills/ai-auto-editor'))
                 / "data" / "program_edits")


def main() -> int:
    ap = argparse.ArgumentParser(description="实时打点 -> 草稿（实时看->决策->改草稿）")
    ap.add_argument("--draft", required=True, help="草稿名或路径")
    ap.add_argument("--marks", help="打点 JSON 路径")
    ap.add_argument("--latest", action="store_true",
                    help="自动取 program_edits 目录里最新的打点文件")
    ap.add_argument("--delete-low", action="store_true", help="顺手删掉低分镜头")
    ap.add_argument("--min-score", type=float, default=realtime_bridge.DEFAULT_MIN_SCORE,
                    help=f"低分阈值，默认 {realtime_bridge.DEFAULT_MIN_SCORE}")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不改草稿不落盘")
    args = ap.parse_args()

    marks_path = Path(args.marks) if args.marks else None
    if args.latest or marks_path is None:
        marks_path = realtime_bridge.latest_marks(PROGRAM_EDITS)
        if marks_path is None:
            raise SystemExit(f"没在 {PROGRAM_EDITS} 找到打点 JSON")
        print(f"自动选用最新打点：{marks_path.name}")
    if not marks_path.exists():
        raise SystemExit(f"找不到打点文件：{marks_path}")

    draft_dir = cli.resolve_draft(args.draft)
    draft = Draft.load(draft_dir)
    marks = realtime_bridge.load_marks(marks_path)
    plan = realtime_bridge.marks_to_operations(marks, draft,
                                               min_score=args.min_score,
                                               delete_low=args.delete_low)

    print(f"草稿 {draft.name} · {draft.declared_duration:.2f}s · "
          f"视频 {len(draft.video_segments)} 段")
    print(f"打点 {Path(marks_path).name} · {len(marks['shots'])} 镜头 · "
          f"切点 {'/'.join(f'{c:.2f}' for c in marks['cut_points']) or '无'}")
    print(f"操作计划：{realtime_bridge.summarize_ops(plan)}")
    for op in plan:
        if op["op"] == "split":
            print(f"  split @ {op['t']:.2f}s" + (f"  （{op['note']}）" if op.get("note") else ""))
        else:
            print(f"  delete_low_score {op['range'][0]:.2f}-{op['range'][1]:.2f}s "
                  f"(score {op['score']:.3f})")

    if args.dry_run:
        print("\n[dry-run] 未改动草稿。")
        return 0

    before = draft.clone()
    ctx = skills.Context(draft=draft, before=before, dry_run=False, log=print)
    skill = skills.get("apply_realtime_marks")
    if skill is None:
        raise SystemExit("技能表没有 apply_realtime_marks——检查 __init__.py 是否导入 skills_realtime")
    report = skill.run(ctx, {
        "marks_path": str(marks_path),
        "delete_low": args.delete_low,
        "min_score": args.min_score,
    })
    if report.get("error"):
        raise SystemExit("技能执行失败：" + report["error"])

    # 落盘
    saved = ctx.draft.save()
    after = Draft.load(saved)

    # 验收
    spec = report.get("verify") or {}
    vrep = verify.from_spec2(spec, before, after)
    print(f"\n执行：{report.get('note')}")
    for line in vrep.lines():
        print("  " + line)
    print(f"草稿已保存：{saved}")
    print(f"片段数：视频 {len(before.video_segments)} -> {len(after.video_segments)} · "
          f"时长 {before.declared_duration:.2f}s -> {after.declared_duration:.2f}s")
    return 0 if vrep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
