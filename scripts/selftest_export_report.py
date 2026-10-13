#!/usr/bin/env python3
"""导出报告模块自测：ExportReport 全流程。

测试项：
  1. stage_start/stage_end 耗时计算正确
  2. log_operation 记录正确
  3. set_* 系列方法数据填充正确
  4. generate() 输出包含所有必需章节
  5. save() 文件存在且可读取
  6. 集成测试：dry_run agent 运行自动生成报告

用法:
  python selftest_export_report.py
"""
from __future__ import annotations
import sys as _portable_sys
from pathlib import Path as _PortablePath
_portable_sys.path.insert(0, str(_PortablePath(__file__).resolve().parents[1] / "lib"))
from aicut.portability import portable_path

import os
import datetime
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli  # noqa: E402
from aicut.export_report import ExportReport  # noqa: E402
from aicut.paths import REPORTS  # noqa: E402

cli.setup()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    work = ROOT / "work" / "selftest"
    case = work / "export_report"
    shutil.rmtree(case, ignore_errors=True)
    case.mkdir(parents=True, exist_ok=True)

    # 输出目录用临时目录，不污染正式 reports/
    test_output = str(case)

    # ========== 测试 1：stage_start/stage_end 耗时计算 ==========
    rep = ExportReport("测试草稿", output_dir=test_output)
    rep.stage_start("测试阶段")
    time.sleep(0.15)
    rep.stage_end("测试阶段")
    dur = rep._stage_duration("测试阶段")
    check("1. stage 耗时计算", dur is not None and dur >= 0.1,
          f"耗时={dur}s (期望>=0.1)")

    # ========== 测试 2：log_operation 记录正确 ==========
    rep.log_operation("删除片段", "0.0-2.0s", "成功", "剪掉 2.00s")
    rep.log_operation("添加字幕", "text_001", "成功", "钩子承诺句")
    check("2. log_operation 记录",
          len(rep._operations) == 2,
          f"记录了 {len(rep._operations)} 条")
    check("2b. log_operation 字段完整",
          all(op.get("timestamp") and op.get("action") and op.get("result")
              for op in rep._operations),
          "每条都有时间戳/动作/结果")

    # ========== 测试 3：set_* 系列方法 ==========
    rep.set_materials([
        {"path": str(ROOT / "04-assets" / "test.mp4"), "name": "test.mp4",
         "size_bytes": 10485760, "duration": 30.5}
    ])
    rep.set_analysis_result(shot_count=8, scene_count=3, vlm_calls=6)
    rep.set_plan_result(decision="剪掉停顿再压时长", style_card="rules", steps=3)
    rep.set_execution_result(draft_path=portable_path('project:06-drafts/test'),
                             skills_called=["delete_silences", "trim_to"],
                             engine_apis=["ripple_delete"])
    rep.set_review_result(iterations=2, scores_per_round=[6.5, 8.2],
                          final_verdict="ready_to_ship", ready_to_ship=True)
    rep.set_export_result(export_method="待用户操作", resolution="1080x1920",
                          fps=30, duration=45.0, export_time=120.5,
                          output_path=None)
    rep.set_open_draft_result(method="待用户操作", timestamp="2026-09-11 10:00:00",
                              result="双击草稿路径")

    check("3. set_analysis_result", rep._analysis.get("shot_count") == 8
          and rep._analysis.get("vlm_calls") == 6)
    check("3b. set_execution_result", len(rep._execution.get("skills_called", [])) == 2)
    check("3c. set_review_result", rep._review.get("ready_to_ship") is True
          and rep._review.get("iterations") == 2)

    # ========== 测试 4：generate() 输出包含所有必需章节 ==========
    text = rep.generate("md")
    required_sections = [
        "视频导出全流程汇总报告",  # 标题
        "草稿名", "引擎版本号", "剪映版本号",  # 元信息
        "素材清单",  # 素材
        "全流程阶段记录",  # 阶段
        "感知", "规划", "执行", "评审", "导出",  # 各阶段
        "操作日志",  # 操作日志
        "所用能力汇总",  # 能力汇总
        "技能列表",  # 技能
    ]
    missing = [s for s in required_sections if s not in text]
    check("4. generate() 包含所有必需章节", len(missing) == 0,
          f"缺失: {missing}" if missing else "全部命中")

    # 验证操作日志表格
    check("4b. 操作日志表格格式",
          "| 时间戳 | 动作 | 对象 | 结果 | 详情 |" in text,
          "表格头存在")

    # ========== 测试 5：save() 文件存在且可读取 ==========
    filepath = rep.save()
    check("5. save() 返回路径", os.path.exists(filepath), filepath)
    saved_text = Path(filepath).read_text(encoding="utf-8")
    check("5b. save() 文件内容可读取且非空",
          len(saved_text) > 100 and "ExportReport" in saved_text,
          f"文件大小={len(saved_text)} 字符")
    check("5c. save() 文件名格式",
          "export_report_" + datetime.datetime.now().strftime("%Y-%m-%d") in filepath and "测试草稿" in filepath,
          filepath)

    # ========== 测试 6：dry_run agent 集成测试 ==========
    from aicut.agent import Agent
    from aicut.draft import Draft

    ref = cli.resolve_draft("月子知识-成片")
    agent_case = work / "agent-export-case"
    shutil.rmtree(agent_case, ignore_errors=True)
    shutil.copytree(ref, agent_case)
    quiet = lambda *_: None  # noqa: E731

    agent = Agent(draft=agent_case, goal="剪掉所有停顿",
                  use_rules=True, use_vision=False, dry_run=True, log=quiet)
    result = agent.run()
    check("6. dry_run agent 跑通", result["ok"], f"步骤={len(result['steps'])}")

    # 确认 ExportReport 被创建并保存
    check("6b. agent 自动创建 ExportReport", agent._export_report is not None)
    if agent._export_report:
        # 报告文件应该已在 run() 尾部 save()
        rep_path = None
        cands = list(Path(REPORTS).glob(
            "export_report_" + datetime.datetime.now().strftime("%Y-%m-%d") + "_*.md"))
        # 优先取 agent 本次生成的文件（draft 名 agent-export-case），避免误取测试草稿报告
        agent_cands = [f for f in cands if "agent-export-case" in f.name]
        if agent_cands:
            rep_path = max(agent_cands, key=lambda f: f.stat().st_mtime)
        elif cands:
            rep_path = max(cands, key=lambda f: f.stat().st_mtime)
        check("6c. 报告文件已自动保存到 reports/", rep_path is not None,
              str(rep_path) if rep_path else "未找到")
        if rep_path:
            rep_content = rep_path.read_text(encoding="utf-8")
            check("6d. 报告内容包含 dry_run 标注",
                  "演算模式" in rep_content,
                  "dry_run 标注存在")
            check("6e. 报告包含技能列表",
                  "技能列表" in rep_content or "调用技能清单" in rep_content,
                  "技能章节存在")

    # 清理
    shutil.rmtree(case, ignore_errors=True)
    shutil.rmtree(agent_case, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
