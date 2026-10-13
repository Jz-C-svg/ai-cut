#!/usr/bin/env python3
r"""实时打点桥接自测：打点解析 / 切分验证 / 技能调用 / 低分删除 / 主体闭环。

全部在 work/selftest 里做副本，不碰真实草稿；45s 测试视频只在 media/ 生成一次。

用法:
  python selftest_realtime.py
  python selftest_realtime.py --keep
"""
from __future__ import annotations
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, realtime_bridge, skills, verify  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []

PROGRAM_EDITS = (Path(portable_path('user:AppData/Local/Doubao/User Data/Default/.doubao/agent_mode/workspace/.user_skills/ai-auto-editor'))
                 / "data" / "program_edits")
MARKS = PROGRAM_EDITS / "打点_capcut_20260911_171544.json"
TEST45 = ROOT / "work" / "selftest" / "media" / "test45.mp4"


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def ensure_test45() -> None:
    """45s 竖屏测试视频（testsrc 彩条 + 正弦音），没有就用 ffmpeg 现造。"""
    if TEST45.exists():
        return
    TEST45.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=duration=45:size=1080x1920:rate=30",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=45",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           "-shortest", str(TEST45)]
    subprocess.run(cmd, check=True)


def build_long_draft(target: Path, seconds: float = 45.0) -> None:
    """复制样本草稿，把视频段拉成一条覆盖 [0, seconds] 的长段（素材指向 test45.mp4）。"""
    ref = cli.resolve_draft("月子知识-成片")
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(ref, target)
    content_path = target / "draft_content.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))

    us = int(seconds * 1_000_000)
    # 1) 视频素材指向 45s 测试视频
    for mat in (content.get("materials") or {}).get("videos") or []:
        mat["path"] = str(TEST45)
        mat["material_name"] = TEST45.name
        mat["duration"] = us
    # 2) 视频轨只留一段 [0, seconds]
    for track in content.get("tracks") or []:
        if track.get("type") != "video":
            continue
        segs = track.get("segments") or []
        if not segs:
            continue
        keep = segs[0]
        keep["target_timerange"] = {"start": 0, "duration": us}
        keep["source_timerange"] = {"start": 0, "duration": us}
        keep["speed"] = 1.0
        track["segments"] = [keep]
    content["duration"] = us
    content_path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="实时打点桥接自测")
    ap.add_argument("--keep", action="store_true", help="保留测试副本")
    args = ap.parse_args()

    ensure_test45()
    work = ROOT / "work" / "selftest"

    # ---------- 1. 打点解析 ----------
    check("打点文件存在", MARKS.exists(), str(MARKS))
    marks = realtime_bridge.load_marks(MARKS)
    check("解析：5 个镜头", len(marks["shots"]) == 5,
          f"{len(marks['shots'])} 个，时长 {marks['shots'][0]['start']}~{marks['shots'][-1]['end']:.2f}")
    expect_cuts = [3.28, 13.28, 21.95, 28.28]
    check("解析：切点 = 镜头 end（除最后一个）", marks["cut_points"] == expect_cuts,
          "/".join(f"{c:.2f}" for c in marks["cut_points"]))
    lows = realtime_bridge.low_score_shots(marks, 0.35)
    check("解析：score<0.35 全部 5 个镜头都算低分", len(lows) == 5,
          "分数 " + "/".join(f"{s['score']:.3f}" for s in lows))
    latest = realtime_bridge.latest_marks(PROGRAM_EDITS)
    check("latest_marks：能扫到最新打点", latest is not None and latest.name == MARKS.name,
          latest.name if latest else "无")
    plan = realtime_bridge.marks_to_operations(marks, min_score=0.35, delete_low=False)
    check("操作计划：4 个 split、0 个删除",
          sum(1 for o in plan if o["op"] == "split") == 4
          and sum(1 for o in plan if o["op"] == "delete_low_score") == 0,
          realtime_bridge.summarize_ops(plan))

    # ---------- 2. 切分验证（纯 split） ----------
    case = work / "rt-split"
    build_long_draft(case)
    draft = Draft.load(case)
    n0 = len(draft.video_segments)
    before = draft.clone()
    from aicut import edit2
    for t in marks["cut_points"]:
        edit2.split_at(draft, t)
    draft.save()
    after = Draft.load(case)
    n1 = len(after.video_segments)
    check("切分：视频段 1 -> 5（+4 个切点）", n1 == n0 + 4, f"{n0} -> {n1}")
    check("切分：总时长不变", abs(after.declared_duration - before.declared_duration) < 0.02,
          f"{before.declared_duration:.3f} -> {after.declared_duration:.3f}")
    check("切分：无重叠", verify.check_no_overlap(after).ok)
    segs = after.video_segments
    cont = all(abs(segs[i].src_out - segs[i + 1].src_in) < 0.02 for i in range(len(segs) - 1)
               if segs[i].src_out is not None)
    check("切分：切点左右源时间连续", cont,
          " | ".join(f"{s.src_in:.2f}~{s.src_out:.2f}" for s in segs))
    check("切分：草稿可重新 load", Draft.load(case).declared_duration > 40)

    # ---------- 3. 技能调用验证（Context 直跑 + from_spec2 验收） ----------
    case2 = work / "rt-skill"
    build_long_draft(case2)
    d2 = Draft.load(case2)
    b2 = d2.clone()
    ctx = skills.Context(draft=d2, before=b2, dry_run=False, log=lambda *_: None)
    skill = skills.get("apply_realtime_marks")
    check("技能已注册", skill is not None and skill.channel == "draft",
          skill.goal if skill else "缺")
    rep = skill.run(ctx, {"marks_path": str(MARKS)})
    check("技能返回 ok 且报告切点", rep.get("ok") and len(rep.get("cut_points", [])) == 4,
          rep.get("note", ""))
    vrep = verify.from_spec2(rep["verify"], b2, d2)
    check("技能验收规格全部通过", vrep.ok, "；".join(c.line() for c in vrep.checks if not c.ok) or "全过")
    check("技能：视频段 1 -> 5", len(d2.video_segments) == 5,
          f"{len(b2.video_segments)} -> {len(d2.video_segments)}")

    # ---------- 4. 低分删除（delete_low + 收紧阈值 0.15） ----------
    case3 = work / "rt-delete"
    build_long_draft(case3)
    d3 = Draft.load(case3)
    b3 = d3.clone()
    ctx3 = skills.Context(draft=d3, before=b3, dry_run=False, log=lambda *_: None)
    rep3 = skills.get("apply_realtime_marks").run(
        ctx3, {"marks_path": str(MARKS), "delete_low": True, "min_score": 0.15})
    vrep3 = verify.from_spec2(rep3["verify"], b3, d3)
    # score<0.15 的镜头：0.094(0-3.28) 与 0.103(21.95-28.28)，共 9.61s
    expect_removed = round(3.28 + 6.33, 2)
    check("低分删除：命中 2 段共 ~9.61s",
          len(rep3["deleted"]) == 2 and abs(rep3["removed"] - expect_removed) < 0.05,
          f"removed {rep3['removed']:.3f}s")
    check("低分删除：验收规格通过", vrep3.ok, "；".join(c.line() for c in vrep3.checks if not c.ok) or "全过")
    check("低分删除：时长缩短 ~9.61s",
          abs((b3.declared_duration - d3.declared_duration) - expect_removed) < 0.1,
          f"{b3.declared_duration:.2f} -> {d3.declared_duration:.2f}")

    # ---------- 5. agent 闭环（perceive->execute->verify->memory） ----------
    case4 = work / "rt-agent"
    build_long_draft(case4)
    from aicut.agent import Agent  # noqa: E402
    agent = Agent(draft=case4, goal="根据实时打点切分镜头", use_rules=True,
                  use_vision=False, dry_run=False, log=lambda *_: None,
                  task="selftest-realtime")
    res = agent._execute_step({
        "skill": "apply_realtime_marks",
        "args": {"marks_path": str(MARKS)},
        "why": "实时打点桥接",
    })
    check("主体闭环：步骤 ok", res.ok, res.error or res.line())
    check("主体闭环：带验证结果", bool(res.checks), f"{len(res.checks)} 条")
    check("主体闭环：草稿真落盘且 5 段",
          Draft.load(case4).declared_duration > 40 and len(Draft.load(case4).video_segments) == 5,
          f"{len(Draft.load(case4).video_segments)} 段")

    if not args.keep:
        for d in ("rt-split", "rt-skill", "rt-delete", "rt-agent"):
            shutil.rmtree(work / d, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
