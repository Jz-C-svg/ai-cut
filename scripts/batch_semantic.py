#!/usr/bin/env python3
r"""批量实测：对 normalized/IMG_5839~5853.mp4 跑镜头档案 + 语义档案。

每个素材做两件事：
  1) shotprof.build(video, use_cloud=False, force=True)   镜头级（逐镜头 VLM）
  2) semantic_profile.build(video, use_cloud=False, force=True)  视频级（一次 VLM 综合）

并发：OLLAMA_NUM_PARALLEL=2，故用多进程 ProcessPoolExecutor(max_workers=2)。
用进程而非线程，是因为 memory.sqlite 的 sqlite3 连接默认 check_same_thread=True，
进程内共享单例连接会跨线程报 ProgrammingError；每进程各自建连接即可规避
（不改动理解层 lib/aicut/semantic_profile.py / shotprof.py / memory.py）。

单个素材失败（含 Ollama 超时）不影响其余，错误记入 report。

输出：work/semantic_batch_report.json
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

NORMALIZED = ROOT / "work" / "normalized"
REPORT = ROOT / "work" / "semantic_batch_report.json"

# 15 个实测素材
STEMS = [f"IMG_58{n}" for n in range(39, 54)]  # 5839..5853


def process_one(stem: str) -> dict:
    """子进程入口：每个进程独立 import 理解层，各自建 memory 连接。"""
    # 子进程需要重新插入 sys.path（spawn 模式下模块顶层会重跑）
    if str(ROOT / "lib") not in sys.path:
        sys.path.insert(0, str(ROOT / "lib"))
    from aicut import semantic_profile, shotprof  # noqa: E402

    video = NORMALIZED / f"{stem}.mp4"
    rec: dict = {"stem": stem, "video": str(video), "ok": False, "error": ""}
    t0 = time.time()
    if not video.exists():
        rec["error"] = "素材不存在"
        return rec
    try:
        # 1) 镜头档案（force=True 重算）
        sp = shotprof.build(video, use_cloud=False, force=True)
        shots = sp.get("shots", []) or []
        summ = sp.get("summary", {}) or {}
        rec["镜头数"] = len(shots)
        rec["vlm_field_completeness"] = summ.get("vlm_field_completeness")

        # 2) 语义档案（内部会读已缓存的 shotprof）
        sem = semantic_profile.build(video, use_cloud=False, force=True)
        rec["semantic_field_completeness"] = sem.get("字段完整率")
        rec["高光候选数"] = len(sem.get("高光候选") or [])
        rec["叙事时间轴段数"] = len(sem.get("叙事时间轴") or [])
        rec["主角"] = sem.get("主角")
        rec["适合结构"] = sem.get("适合结构")
        rec["duration"] = sem.get("duration")
        rec["ok"] = True
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["trace"] = traceback.format_exc(limit=3)[-800:]
    rec["耗时秒"] = round(time.time() - t0, 1)
    return rec


def main() -> int:
    stems = STEMS
    results: list[dict] = []
    print(f"[batch] 共 {len(stems)} 个素材，多进程并发=2，开始 {time.strftime('%H:%M:%S')}", flush=True)
    with ProcessPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(process_one, s): s for s in stems}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                r = fut.result()
            except Exception as exc:  # noqa: BLE001
                r = {"stem": s, "ok": False, "error": f"future: {exc}"}
            results.append(r)
            mark = "OK " if r.get("ok") else "ERR"
            print(f"[batch][{mark}] {r.get('stem')}  镜头={r.get('镜头数','-')}  "
                  f"VLM完整率={r.get('vlm_field_completeness','-')}  "
                  f"语义完整率={r.get('semantic_field_completeness','-')}  "
                  f"高光={r.get('高光候选数','-')}  "
                  f"轴段={r.get('叙事时间轴段数','-')}  "
                  f"{r.get('耗时秒','-')}s  {r.get('error','')[:60]}", flush=True)

    results.sort(key=lambda x: x["stem"])
    ok = [r for r in results if r.get("ok")]
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(results),
        "ok_count": len(ok),
        "fail_count": len(results) - len(ok),
        "concurrency": "processes=2",
        "items": results,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[batch] 完成 {len(ok)}/{len(results)}，报告 -> {REPORT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
