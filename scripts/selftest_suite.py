#!/usr/bin/env python3
r"""总自测：一条命令把 v2 该有的能力全体检一遍。

  · 内核自测（读/编辑/diff/回滚/断言/记账）
  · 主体自测（规划/守卫/执行/验证/学习）
  · 环境体检 doctor
  · 观测台接口
  · 界面通道守卫（剪映没开时必须拒绝）
  · 数据学习（用合成表跑一遍）
  · 演示学习（用合成记录跑一遍，--llm 才真的调模型蒸馏）

用法:
  python selftest_suite.py
  python selftest_suite.py --llm --semantic
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, guard, skills, ui  # noqa: E402
from aicut.paths import python_exe  # noqa: E402

cli.setup()

PY = python_exe()
SCRIPTS = ROOT / "scripts"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def run(script: str, *args: str, timeout: int = 900) -> tuple[int, str]:
    proc = subprocess.run([PY, str(SCRIPTS / script), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser(description="v2 总自测")
    ap.add_argument("--llm", action="store_true", help="连模型的部分（主体规划、演示蒸馏）也跑")
    ap.add_argument("--semantic", action="store_true", help="额外跑一次语义断言（慢）")
    args = ap.parse_args()

    rc, out = run("selftest_core.py")
    check("内核自测", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
    rc, out = run("selftest_agent.py", *(["--llm"] if args.llm else []))
    check("主体自测", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")

    rc, out = run("selftest_edit2.py")
    check("精确原语自测", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
    rc, out = run("selftest_tuning.py")
    check("调参层自测", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
    rc, out = run("selftest_learn.py")
    check("学习环自测", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
    rc, out = run("doctor.py", "--json")
    try:
        data = json.loads(out[out.index("{"):])
        check("环境体检", data["fail"] == 0, f"失败 {data['fail']}，警告 {data['warn']}")
    except Exception as exc:  # noqa: BLE001
        check("环境体检", False, f"{type(exc).__name__}: {out[-120:]}")

    rc, out = run("agent_console.py", "--once")
    try:
        state = json.loads(out)
        check("观测台接口", "stats" in state and "latest_task" in state,
              f"任务 {state.get('latest_task')}")
    except Exception as exc:  # noqa: BLE001
        check("观测台接口", False, f"{type(exc).__name__}")

    # v5.0.0（P1）：剪映没开时自动拉起，让界面通道链路做真实验证而非只走守卫
    if not ui.info().get("running"):
        res = ui.launch(wait=45)
        check("界面通道自启剪映", bool(res.get("running")), (
            f"启动成功（{res.get('seconds', 0)}s）" if res.get("running")
            else res.get("error", "启动失败")))
    else:
        check("界面通道自启剪映", True, "剪映已在运行，跳过启动")

    info = ui.info()
    check("界面通道可用性检测", isinstance(info, dict) and "running" in info,
          f"剪映{'在跑' if info.get('running') else '没开'}")
    blocked = guard.precheck(skills.get("ui_press"), type("A", (), {"draft": None})())
    check("界面通道守卫", bool(blocked) == (not info.get("running")), blocked or "剪映在跑，允许执行")

    tmp_csv = ROOT / "work" / "selftest" / "suite-data.csv"
    tmp_csv.parent.mkdir(parents=True, exist_ok=True)
    rows = ["标题,风格,播放量,完播率,点赞,评论,分享,涨粉"]
    for i in range(24):
        style = "koubo-zhishi" if i % 2 == 0 else "kadian-jinji"
        finish = 22 if style == "koubo-zhishi" else 31
        rows.append(f"自测视频{i:02d},{style},{3000 + i * 97},{finish},{80 + i},5 + 0,3,1")
    tmp_csv.write_text("\n".join(rows), encoding="utf-8-sig")
    rc, out = run("learn_from_data.py", "--data", str(tmp_csv), "--json")
    try:
        data = json.loads(out[out.index("{"):])
        ok = bool(data.get("建议")) and data.get("基线", {}).get("views") is not None
        check("数据学习", ok, f"{len(data.get('建议') or [])} 条建议，基线播放 {data['基线'].get('views')}")
    except Exception as exc:  # noqa: BLE001
        check("数据学习", False, f"{type(exc).__name__}: {out[-120:]}")

    from aicut import diff as diff_mod  # noqa: E402
    from aicut.draft import Draft  # noqa: E402
    import importlib  # noqa: E402

    rec = importlib.import_module("demo_recorder") if str(SCRIPTS) in sys.path else None
    if rec is None:
        sys.path.insert(0, str(SCRIPTS))
        rec = importlib.import_module("demo_recorder")
    tmp_records = ROOT / "work" / "selftest" / "demos"
    shutil.rmtree(tmp_records, ignore_errors=True)
    original_records = rec.RECORDS
    rec.RECORDS = tmp_records
    try:
        ref = ROOT / "06-drafts" / "月子知识-成片" / "draft_content.json"
        before = ref.read_text(encoding="utf-8")
        doc = json.loads(before)
        for track in doc["tracks"]:
            for seg in track["segments"]:
                rng = seg.get("target_timerange")
                if rng:
                    rng["start"] = max(0, rng["start"] - 500_000)
        after = json.dumps(doc, ensure_ascii=False)
        ev = rec.record_change(ROOT / "06-drafts" / "月子知识-成片" / "draft_content.json",
                               before, after, None, 1)
        check("演示学习：留证据", Path(ev["dir"], "info.json").exists(), "；".join(ev["summary"][:2]))
        if args.llm:
            result = rec.distill()
            check("演示学习：蒸馏出偏好", bool(result.get("ok") and result.get("写入偏好")),
                  f"{len(result.get('写入偏好') or [])} 条")
    finally:
        rec.RECORDS = original_records
        shutil.rmtree(tmp_records, ignore_errors=True)
        shutil.rmtree(tmp_csv.parent, ignore_errors=True)

    # ---- 新增批次：画面质量 / 导出报告 / Critic 循环 / 多软件适配 / 重说检测 / v3.3 融合 ----
    for _script, _label in [
        ("selftest_cleancheck.py", "画面干净检查+比对"),
        ("selftest_export_report.py", "导出全流程报告"),
        ("selftest_editor_critic.py", "Editor-Critic 循环"),
        ("selftest_bridges.py", "多剪辑软件适配器"),
        ("selftest_retake.py", "重说口误检测"),
        ("selftest_v33.py", "v3.3 融合回归"),
        ("selftest_voiceover.py", "Edge-TTS 配音接入"),
        ("selftest_skill_archive.py", "技能存档体系"),
        ("selftest_keyframes.py", "关键帧别名+easing"),
        ("selftest_semantic.py", "视频理解智能化"),
        ("selftest_visual.py", "视觉智能适配引擎"),
        ("selftest_videofinger.py", "模板化剪辑引擎"),
        ("selftest_router.py", "多接口AI大脑路由层"),
        ("selftest_resilience.py", "上线稳定性加固"),
    ]:
        _rc, _out = run(_script, timeout=1800)
        _last = _out.strip().splitlines()[-1] if _out.strip() else ""
        check(_label, _rc == 0, _last[:80])

    if args.semantic:
        video = ROOT / "work" / "normalized" / "月子知识-0299.mp4"
        if video.exists():
            rc, out = run("semantic_check.py", "--video", str(video), "--at", "2.5",
                          "--ask", "这一帧里能看到人物吗", timeout=900)
            check("语义断言", rc == 0, out.strip().splitlines()[0] if out.strip() else "")
        else:
            check("语义断言", False, "缺少测试素材")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
