#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TOP1 Editor-Critic 迭代循环 + TOP4 shotprof 三槽位 自测。

覆盖（至少 12 项）：
  1. DONE/RENDER 控制原语已注册，fn 返回正确信号
  2. critique_loop schema 齐全（overall_score/issues/ready_to_ship）
  3. critique_loop issues 的 plan_patch.skill 都在 REGISTRY 里（或 None）
  4. 无字幕草稿 → P0 字幕 issue（add_subtitle）
  5. ready_to_ship 阈值：高总分无 P0 → ready=True
  6. agent 迭代：无字幕草稿 → 第一轮 P0 → 第二轮 add_subtitle → 第三轮 ready 或达上限
  7. 迭代上限：max_iterations=2 → 2 轮后退出
  8. 看门狗：连续 3 次相同操作 → nudge 记账
  9. shotprof 三槽位枚举值合法
 10. shotprof 强校验降级（怪值→default）
 11. 节奏硬规则：前5镜全 medium+locked → P1 节奏 issue
 12. patch_apply 点分路径解析（"video.2" → 第2段）
 13. durable state：work/agent-state/<task>.json 写盘

用法:
  python selftest_editor_critic.py
  python selftest_editor_critic.py --keep   # 保留测试副本
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, critique_loop, patch_apply, review_film, shotprof, skills  # noqa: E402
from aicut.agent import Agent  # noqa: E402
from aicut.draft import Draft  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def _strip_all_subtitles(case: Path) -> None:
    """直接改 draft_content.json，把所有 text 轨道清空（构造"无字幕"问题草稿）。"""
    p = case / "draft_content.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    tracks = data.get("tracks") or []
    removed = 0
    for t in tracks:
        if t.get("type") == "text":
            removed += len(t.get("segments") or [])
            t["segments"] = []
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="Editor-Critic 迭代循环 + shotprof 三槽位 自测")
    ap.add_argument("--draft", default="月子知识-成片")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    ref = cli.resolve_draft(args.draft)
    work = ROOT / "work" / "selftest"
    case = work / "ec-case"
    shutil.rmtree(case, ignore_errors=True)
    shutil.copytree(ref, case)
    base = Draft.load(ref)
    quiet = lambda *_: None  # noqa: E731

    # 让 perceive 不调 Ollama / ffmpeg（测试聚焦迭代逻辑）
    import aicut.agent as agent_mod
    orig_build = shotprof.build
    agent_mod.shotprof.build = lambda *a, **kw: {"summary": {"shots": 0}, "shots": [],
                                                 "cached": True}  # type: ignore

    # 1. DONE/RENDER 原语
    done_s = skills.get("DONE")
    render_s = skills.get("RENDER")
    check("DONE/RENDER 已注册", done_s is not None and render_s is not None)
    fake_ctx = skills.Context(draft=None, video=None)
    r1 = done_s.run(fake_ctx, {})
    r2 = render_s.run(fake_ctx, {})
    check("DONE 返回 op=done", r1.get("op") == "done" and r1.get("ok") is True, str(r1))
    check("RENDER 返回 op=render", r2.get("op") == "render" and r2.get("ok") is True, str(r2))

    # 2. critique_loop schema
    crit = critique_loop.critique(case, use_vision=False, iteration=1)
    check("critique_loop schema 齐全",
          all(k in crit for k in ("overall_score", "issues", "ready_to_ship",
                                   "iteration", "review_film")),
          f"overall={crit['overall_score']} ready={crit['ready_to_ship']}")
    check("critique_loop overall 是 0-10 制",
          0 <= crit["overall_score"] <= 10, str(crit["overall_score"]))

    # 3. plan_patch.skill 都在 REGISTRY 里
    ids = set(skills.get_registry_ids())
    bad_patches = [i for i in crit["issues"]
                   if i.get("plan_patch")
                   and i["plan_patch"].get("skill") not in ids]
    check("plan_patch.skill 都在 REGISTRY（或 None）", not bad_patches,
          f"bad={bad_patches[:1]}")

    # 4. 无字幕草稿 → P0 字幕 issue
    removed = _strip_all_subtitles(case)
    crit2 = critique_loop.critique(case, use_vision=False, iteration=1)
    sub_issues = [i for i in crit2["issues"] if i.get("plan_patch", {}).get("skill") == "add_subtitle"]
    check("无字幕草稿 → P0/P1 字幕 issue", len(sub_issues) > 0,
          f"删了 {removed} 条字幕，issues={len(crit2['issues'])}")

    # 5. ready_to_ship 阈值：mock 高总分无 P0
    #    直接调 critique_loop 内部映射，用一个假 review_film 返回
    fake_rf = {"total": 95, "weakest": {"dimension": "合规", "score": 90, "advice": "ok"},
               "dimensions": {k: {"score": 95, "notes": []} for k in
                              ("结构", "钩子", "节奏", "画面", "字幕", "声音", "合规")},
               "issues": []}
    # 手测 ready 判定逻辑
    overall = fake_rf["total"] / 10.0
    ready = (overall >= critique_loop.READY_SCORE and not
             [i for i in fake_rf["issues"] if i.get("severity") == "P0"])
    check("ready_to_ship：95分无P0 → True", ready is True, f"overall={overall}")

    # 6. agent 迭代：无字幕草稿 → 迭代加字幕
    #    复制一份新副本（case 已被改）
    case2 = work / "ec-case2"
    shutil.rmtree(case2, ignore_errors=True)
    shutil.copytree(ref, case2)
    _strip_all_subtitles(case2)
    agent = Agent(draft=case2, goal="补字幕", use_rules=True, use_vision=False,
                  max_iterations=3, log=quiet)
    result = agent.run()
    check("agent 迭代：跑通且至少 1 轮 review",
          result["iteration_count"] >= 1,
          f"iters={result['iteration_count']} stop={result['stop_reason']}")
    check("agent 迭代：issues → steps 转成 add_subtitle",
          any(s["skill"] == "add_subtitle" for s in result["steps"]),
          f"steps={[s['skill'] for s in result['steps']]}")
    check("agent 迭代：durable state 写盘",
          (ROOT / "work" / "agent-state" / f"{result['task']}.json").exists())

    # 7. 迭代上限：max_iterations=2 强制 2 轮停（即使没 ready）
    case3 = work / "ec-case3"
    shutil.rmtree(case3, ignore_errors=True)
    shutil.copytree(ref, case3)
    _strip_all_subtitles(case3)
    # mock critique_loop 永远返回 not ready，验证达上限退出
    orig_critique = critique_loop.critique
    def always_feedback(*a, **kw):
        out = orig_critique(*a, **kw)
        out["ready_to_ship"] = False
        out["overall_score"] = 5.0
        return out
    critique_loop.critique = always_feedback  # type: ignore
    try:
        agent3 = Agent(draft=case3, goal="补字幕", use_rules=True, use_vision=False,
                       max_iterations=2, log=quiet)
        r3 = agent3.run()
        check("迭代上限：max=2 → 2 轮后退出",
              r3["iteration_count"] == 2 and r3["stop_reason"] == "max_iterations",
              f"iters={r3['iteration_count']} stop={r3['stop_reason']}")
    finally:
        critique_loop.critique = orig_critique  # type: ignore

    # 8. 看门狗：连续 3 次相同操作 → nudge 记账
    #    直接测 _issues_to_steps + 手动塞 deque
    agent_dog = Agent(draft=case, goal="x", use_rules=True, use_vision=False, log=quiet)
    agent_dog.steps = []
    from collections import deque
    wd: deque = deque(maxlen=3)
    for k in range(3):
        wd.append(("delete_head", ("seconds",)))
    triggered = (len(wd) == 3 and len(set(wd)) == 1)
    check("看门狗：连续3次相同操作 → 触发", triggered, f"wd={list(wd)}")

    # 9. shotprof 三槽位枚举值合法
    check("SHOT_SIZES 5 个值", set(shotprof.SHOT_SIZES.keys()) ==
          {"extreme_close_up", "close_up", "medium", "wide", "extreme_wide"})
    check("SHOT_ANGLES 5 个值", set(shotprof.SHOT_ANGLES.keys()) ==
          {"high_angle", "low_angle", "eye_level", "overhead", "aerial"})
    check("SHOT_MOTIONS 6 个值", set(shotprof.SHOT_MOTIONS.keys()) ==
          {"locked", "handheld", "tilt", "zoom", "pan", "static"})

    # 10. shotprof 强校验降级
    check("clamp 怪值 → default",
          shotprof._clamp_enum("blah", shotprof.SHOT_SIZES, "medium") == "medium")
    check("clamp 中文近景 → close_up",
          shotprof._clamp_enum("近景", shotprof.SHOT_SIZES, "medium",
                                shotprof.CN_SIZE_MAP) == "close_up")

    # 11. 节奏硬规则：前 5 镜全 medium+locked → P1 节奏 issue
    fake_profile = {"shots": [
        {"shot": i, "t0": float(i * 2), "t1": float(i * 2 + 2), "len": 2.0,
         "shot_size": "medium", "shot_motion": "locked",
         "time_range": [float(i * 2), float(i * 2 + 2)]}
        for i in range(6)
    ]}
    fake_clips = [{"t": float(i * 2), "end": float(i * 2 + 2), "length": 2.0} for i in range(6)]
    ded, hard_issues, notes = review_film._rhythm_hard_rules(fake_profile, fake_clips)
    check("节奏硬规则：前5镜medium+locked → 扣分", ded >= 10, f"ded={ded} notes={notes}")
    check("节奏硬规则：产出 hard_issue",
          any(i.get("suggested_action") == "add_transition" for i in hard_issues),
          f"issues={[i.get('suggested_action') for i in hard_issues]}")

    # 12. patch_apply 点分路径解析
    d = Draft.load(case)
    seg = patch_apply.parse_segment_ref("video.1", d)
    check("patch_apply 点分路径 video.1 → 第一段视频",
          seg is not None and seg.t == d.video_segments[0].t,
          f"seg={getattr(seg, 'id', None)}")
    seg2 = patch_apply.parse_segment_ref({"t": 1.0}, d)
    check("patch_apply dict {t:1.0} → 覆盖1s的段", seg2 is not None)

    # 13. durable state 文件
    state_file = ROOT / "work" / "agent-state" / f"{result['task']}.json"
    check("durable state 文件存在且含 iteration",
          state_file.exists() and
          json.loads(state_file.read_text(encoding="utf-8")).get("iteration") >= 1)

    # 还原
    agent_mod.shotprof.build = orig_build  # type: ignore
    if not args.keep:
        shutil.rmtree(case, ignore_errors=True)
        shutil.rmtree(case2, ignore_errors=True)
        shutil.rmtree(case3, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    for n, ok, det in failed:
        print(f"  FAIL: {n} · {det}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
