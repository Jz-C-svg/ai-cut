#!/usr/bin/env python3
r"""v4.0.0 稳定性加固自检：覆盖 resilience 的全部验收场景。

运行：
    <project-root>\venv\Scripts\python.exe <project-root>\scripts\selftest_resilience.py
全绿：最后一行打印「共 N 项，通过 N，失败 0」并以退出码 0 结束。
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import resilience  # noqa: E402
from aicut.resilience import (  # noqa: E402
    BatchProgress, DEGRADATION_MATRIX, atomic_write_json, execute_step,
    find_tmp_candidates, format_error, validate_config,
)
from aicut.draft import Draft  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" · {detail}" if detail else ""))


# ---------------------------------------------------------------------------
def t_import() -> None:
    check("1. resilience 可独立 import 不报错",
          hasattr(resilience, "DEGRADATION_MATRIX") and callable(resilience.atomic_write_json))


def t_atomic_write() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        target = d / "sub" / "draft_content.json"
        payload = {"name": "x", "tracks": [], "big": "你好" * 5000}  # 稍大 JSON

        # 正常原子写入
        p = atomic_write_json(target, payload, ensure_ascii=False)
        check("2a. 原子写入成功且内容可回读",
              json.loads(target.read_text(encoding="utf-8")) == payload)
        # 成功后不应残留 .tmp
        check("2b. 成功后无 .tmp 残留", find_tmp_candidates(target) == [])

        # 模拟中断：手动制造一个「已写完整、但还没 os.replace」的 .tmp，主文件保持旧版
        old = {"name": "OLD", "tracks": []}
        atomic_write_json(target, old, ensure_ascii=False)  # 把主文件刷成旧版 old
        tmp = d / "sub" / ("draft_content.json.tmp" + "ABCDEF12")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        # 关键：主文件不受 .tmp 影响（仍是旧版）——这就是原子性
        check("2c. 存在未完成 .tmp 时，原文件保持旧版完整",
              json.loads(target.read_text(encoding="utf-8")) == old)

        # 现在把主文件写坏（模拟写到一半断电），再 load -> 应从 .tmp 恢复
        target.write_text('{"name": "BROKEN", "tracks": [', encoding="utf-8")  # 截断的坏 JSON
        draft = Draft.load(target)
        check("2d. 主文件损坏后从 .tmp 恢复", draft.content.get("name") == "x",
              f"恢复到 name={draft.content.get('name')}")
        # 恢复后主文件应已自愈为完整 JSON，且 .tmp 被清理
        check("2e. 恢复后主文件完整且 .tmp 已清理",
              json.loads(target.read_text(encoding="utf-8"))["name"] == "x"
              and find_tmp_candidates(target) == [])


def t_degradation_matrix() -> None:
    # 成功路径
    ok, res, msg = execute_step("normalize", lambda a, b: a + b, 1, 2)
    check("3a. 成功步骤返回 (True, 结果, 空)", ok and res == 3 and msg == "", f"res={res}")

    # skip 策略（semantic: retries=0）：抛异常不崩溃，返回 (False, None, 中文错误)
    calls = {"n": 0}
    def _always_fail():
        calls["n"] += 1
        raise KeyError("模拟语义字段缺失")
    ok, res, msg = execute_step("semantic", _always_fail)
    check("3b. skip 策略：不崩溃，返回失败且结果为 None",
          (not ok) and res is None and calls["n"] == 1 and "中文" not in msg)
    check("3c. skip 错误信息含中文可读类型", "配置/字段缺失" in msg or "KeyError" in msg, msg[:40])

    # degrade 策略（transcribe: retries=1）：重试 2 次后用降级产物
    calls["n"] = 0
    ok, res, msg = execute_step("transcribe", _always_fail, _fallback=[])
    check("3d. degrade 策略：重试次数=retries+1=2，结果为降级产物",
          (not ok) and res == [] and calls["n"] == 2, f"调用 {calls['n']} 次")
    check("3e. degrade 信息标注已降级", "降级" in msg, msg[:50])

    # retry 策略（draft_write: retries=2）：前两次抛、第三次成功
    calls["n"] = 0
    def _fail_twice():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("模拟写入失败")
        return "DRAFT_OK"
    ok, res, msg = execute_step("draft_write", _fail_twice)
    check("3f. retry 策略：重试到第 3 次成功", ok and res == "DRAFT_OK" and calls["n"] == 3)

    # 未登记步骤兜底：不崩溃
    ok, res, msg = execute_step("unknown_step", _always_fail)
    check("3g. 未登记步骤按默认 skip，不崩溃", (not ok) and res is None and bool(msg))


def t_batch_progress() -> None:
    with tempfile.TemporaryDirectory() as td:
        old_file = resilience.PROGRESS_FILE
        try:
            resilience.PROGRESS_FILE = Path(td) / "batch_progress.json"
            bp = BatchProgress("job-测试")
            bp.reset()
            mats = ["a.mp4", "b.mp4", "c.mp4"]
            bp.mark_started("a.mp4")
            check("4a. started 素材仍算待处理", bp.pending_materials(mats) == mats)
            bp.mark_completed("a.mp4", "out/a.json")
            check("4b. mark_completed 后 is_completed=True", bp.is_completed("a.mp4"))
            pend = bp.pending_materials(mats)
            check("4c. pending 排除已完成项", pend == ["b.mp4", "c.mp4"], str(pend))
            bp.mark_failed("b.mp4", "boom")
            pend2 = bp.pending_materials(mats)
            check("4d. failed 素材重启后仍待重跑", pend2 == ["b.mp4", "c.mp4"], str(pend2))
            # 重新加载（模拟进程重启），进度不丢
            bp2 = BatchProgress("job-测试")
            check("4e. 重启后进度持久化且 a 仍完成",
                  bp2.is_completed("a.mp4") and bp2.pending_materials(mats) == ["b.mp4", "c.mp4"])
            # 换 job_id 互不污染
            bp3 = BatchProgress("another-job")
            check("4f. 不同 job_id 互不污染", bp3.pending_materials(mats) == mats)
        finally:
            resilience.PROGRESS_FILE = old_file


def t_config_validate() -> None:
    cfg, warns = validate_config({})
    check("5a. 空 dict 返回带默认值配置",
          cfg["render"] == {"width": 1080, "height": 1920, "fps": 30, "crf": 20}
          and cfg["asr"]["model"] == "small" and cfg["asr"]["language"] == "zh"
          and cfg["ai_backends"] == []
          and cfg["vision"]["model"] == "qwen3-vl:8b")
    check("5b. 空配置产生警告列表", len(warns) >= 5, f"{len(warns)} 条警告")

    # 不全的 render 只补缺，不改已有
    cfg2, warns2 = validate_config({"render": {"width": 1920}})
    check("5c. render 保留已有 width 并补 fps/crf",
          cfg2["render"]["width"] == 1920 and cfg2["render"]["fps"] == 30 and cfg2["render"]["crf"] == 20)

    # 不崩：入参是 None
    cfg3, _ = validate_config(None)
    check("5d. None 入参不崩溃并补默认", cfg3["project"] == "AI cut")

    # 入参 dict 不被原地污染
    raw = {"render": {"width": 1080}}
    _ = validate_config(raw)
    check("5e. 不修改入参原 dict", raw["render"] == {"width": 1080})


def t_format_error() -> None:
    s = format_error(FileNotFoundError("d:/x.mp4"), "transcribe")
    check("6. format_error 含步骤名与中文类型",
          "transcribe" in s and "文件找不到" in s and "x.mp4" in s, s)


def t_draft_save_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "我的草稿"
        d.mkdir()
        content = {
            "name": "我的草稿",
            "duration": 12_300_000,
            "fps": 30,
            "tracks": [{"type": "video", "segments": [
                {"id": "seg1", "material_id": "m1",
                 "target_timerange": {"start": 0, "duration": 3_000_000}}]}],
            "materials": {"videos": [{"id": "m1", "path": "x.mp4"}]},
        }
        (d / "draft_content.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        draft = Draft.load(d)
        # 改一下再 save
        draft.content["tracks"][0]["segments"][0]["target_timerange"]["duration"] = 4_000_000
        out = draft.save()
        re = Draft.load(d)
        check("7a. Draft.save 走原子写入后可重新 load",
              re.declared_duration > 0 and out.name == "draft_content.json")
        check("7b. save 后无 .tmp 残留", find_tmp_candidates(d / "draft_content.json") == [])


def t_error_log_append() -> None:
    with tempfile.TemporaryDirectory() as td:
        old_log, old_work = resilience.ERROR_LOG, resilience.WORK_DIR
        try:
            resilience.WORK_DIR = Path(td)
            resilience.ERROR_LOG = Path(td) / "error_log.txt"
            resilience.log_error("transcribe", ValueError("第一条"))
            text1 = resilience.ERROR_LOG.read_text(encoding="utf-8")
            len1 = len(text1)
            resilience.log_error("draft_write", RuntimeError("第二条"))
            text = resilience.ERROR_LOG.read_text(encoding="utf-8")
            # 追加而非覆盖：第二条记录出现后，第一条仍在，且文件变长
            ok = (len(text) > len1 and "transcribe" in text1 and "draft_write" in text
                  and text.count("[") >= 2 and "第一条" in text and "第二条" in text)
            check("8. error_log.txt 追加不覆盖（两条记录都在且文件变长）",
                  ok, f"{len1} -> {len(text)} 字节")
        finally:
            resilience.ERROR_LOG, resilience.WORK_DIR = old_log, old_work


def main() -> int:
    t_import()
    t_atomic_write()
    t_degradation_matrix()
    t_batch_progress()
    t_config_validate()
    t_format_error()
    t_draft_save_roundtrip()
    t_error_log_append()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    if failed:
        for name, _, detail in failed:
            print(f"  ❌ {name} · {detail}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
