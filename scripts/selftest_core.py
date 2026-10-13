#!/usr/bin/env python3
r"""v2 内核自测：读草稿 / 编辑原语 / diff / 快照回滚 / 验证断言 / 记忆库。

不依赖剪映是否开着，也不碰真实草稿——全部在 work/selftest 里做副本。

用法:
  python selftest_core.py
  python selftest_core.py --draft 月子知识-成片 --keep
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, diff as diff_mod, edit, snapshot as snap_mod, verify  # noqa: E402
from aicut.draft import Draft  # noqa: E402
from aicut.memory import open_memory  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="v2 内核自测")
    ap.add_argument("--draft", default="月子知识-成片", help="拿哪个草稿当样本")
    ap.add_argument("--keep", action="store_true", help="保留测试副本")
    args = ap.parse_args()

    ref = cli.resolve_draft(args.draft)
    work = ROOT / "work" / "selftest"
    case = work / "core"
    mem = open_memory()
    task = f"selftest-core-{ref.name}"
    shutil.rmtree(case, ignore_errors=True)
    shutil.copytree(ref, case)
    print(f"样本：{ref}")

    base = Draft.load(ref)
    original = base.content
    print(f"原始：{base.declared_duration:.3f}s · 轨道 {base.track_types} · 片段 {len(list(base.all_segments()))}")

    # 1. 读取
    check("读草稿：时长与剪映一致",
          base.declared_duration > 0 and abs(base.max_end - base.declared_duration) < 0.02,
          f"{base.declared_duration:.3f}s / max_end {base.max_end:.3f}s")
    check("读草稿：视频轨与字幕轨都能解析",
          len(base.video_segments) > 0 and len(base.text_segments) > 0,
          f"video {len(base.video_segments)} · text {len(base.text_segments)}")
    check("读草稿：素材路径可反查",
          all(s.material_path() for s in base.video_segments),
          base.video_segments[0].material_path() if base.video_segments else "无")

    # 2. 快照
    snap = snap_mod.snapshot(case, note="selftest")
    before_file = (case / "draft_content.json").read_bytes()
    check("快照：文件已复制且索引已写入",
          Path(snap["path"]).exists() and len(snap_mod.snapshots(case.name)) > 0, snap["id"])

    # 3. 演算不落盘
    dry = Draft.load(case)
    rep_dry = edit.ripple_delete(dry, 0.0, 2.0, dry_run=True)
    check("演算：--dry-run 不改文件", (case / "draft_content.json").read_bytes() == before_file,
          f"报告时长 {rep_dry['duration_after']:.3f}s")

    # 4. 真删开头 2 秒
    live = Draft.load(case)
    rep = edit.ripple_delete(live, 0.0, 2.0)
    live.save()
    after = Draft.load(case)
    check("删除：时长精确 -2.000s", abs(after.declared_duration - (base.declared_duration - 2.0)) < 0.01,
          f"{base.declared_duration:.3f} -> {after.declared_duration:.3f}")
    check("删除：后续片段整体前移", verify.check_shift(base, after, -2.0).ok, f"位移片段 {rep['shifted']} 个")
    check("删除：无残留重叠", verify.check_no_overlap(after).ok)
    pairs = [(s, next(b for b in base.video_segments if b.id == s.id))
             for s in after.video_segments if s.id in {v.id for v in base.video_segments}]
    check("删除：视频源入点同步前进 2s",
          any(s.src_in is not None and abs(s.src_in - (b.src_in + 2.0)) < 0.02 for s, b in pairs),
          f"源入点 {[round(s.src_in or 0, 2) for s in after.video_segments]}")
    mem.add_episode(task=task, kind="edit", step=1, goal="删除开头 2 秒", detail=rep,
                    evidence=str(case), ok=True)

    # 5. diff
    d = diff_mod.diff(base, after)
    check("diff：报出 -2.000s 与整体位移",
          abs(d["duration"]["delta"] + 2.0) < 0.01 and d["uniform_shift"] is not None,
          "；".join(d["summary"][:2]))

    # 6. 声明式验证
    spec = {"duration_delta": [-2.01, -1.99], "shift": -2.0, "no_overlap": True,
            "min_segment_len": {"video": 0.5}, "track_exists": ["video", "text"]}
    rep_v = verify.from_spec(spec, base, after)
    check("验证：声明式规格全部通过", rep_v.ok,
          "；".join(c.name for c in rep_v.checks if not c.ok) or "全部通过")

    # 7. 回滚
    restored = snap_mod.restore(snap["id"], backup=True)
    back = Draft.load(case)
    check("回滚：文件字节级还原", back.content == original, f"回到 {restored['duration']:.3f}s")
    check("回滚：回滚动作也留了后路", len(snap_mod.snapshots(case.name)) >= 2,
          f"快照数 {len(snap_mod.snapshots(case.name))}")

    # 8. 段内切除 -> 该片段必须被切成两段
    cut = Draft.load(case)
    rep_cut = edit.ripple_delete(cut, 1.0, 2.0)
    cut.save()
    cut_after = Draft.load(case)
    check("切除：区间落在单个片段内部时切成两段", len(rep_cut["split"]) >= 1,
          f"split {len(rep_cut['split'])} · 视频片段 {len(cut_after.video_segments)}")
    check("切除：无重叠且时长正确",
          verify.check_no_overlap(cut_after).ok
          and abs(cut_after.declared_duration - (base.declared_duration - 1.0)) < 0.01,
          f"{cut_after.declared_duration:.3f}s")
    snap_mod.restore(snap["id"], backup=False)

    # 9. 记忆库
    stats = mem.stats()
    check("记忆库：快照可查询", stats["snapshots"] >= 2, f"快照 {stats['snapshots']} 个")
    check("记忆库：任务步骤已记账", any(e["task"] == task for e in mem.episodes(task)),
          f"episodes {stats['episodes']} 条 · {stats['db']}")

    if not args.keep:
        shutil.rmtree(case, ignore_errors=True)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
