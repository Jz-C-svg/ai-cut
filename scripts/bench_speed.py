#!/usr/bin/env python3
r"""v3.2 提速清单统一基准测试（before/after 实测）。

用法（务必用 venv python，并设好 PYTHONPATH）:
  set PYTHONPATH=<project-root>\lib
  "<project-root>\venv\Scripts\python.exe" scripts\bench_speed.py b1
  "<project-root>\venv\Scripts\python.exe" scripts\bench_speed.py shot [--force]
  "<project-root>\venv\Scripts\python.exe" scripts\silence
  "<project-root>\venv\Scripts\python.exe" scripts\bench_speed.py transcribe <video>

所有计时用 time.perf_counter()，关键项取多次平均。结果打印为易读文本，
同时追加写入 work/bench-v32.jsonl（每跑一次追加一行 JSON，便于汇总 before/after）。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
os.environ.setdefault("PYTHONPATH", str(ROOT / "lib"))

BENCH_LOG = ROOT / "work" / "bench-v32.jsonl"
MATERIAL = ROOT / "work" / "normalized" / "月子知识-0299.mp4"
MATERIAL2 = ROOT / "work" / "normalized" / "月子知识-0310.mp4"


def _rec(tag: str, data: dict) -> None:
    BENCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"tag": tag, "t": time.strftime("%Y-%m-%d %H:%M:%S"), **data}
    with BENCH_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("[BENCH] " + json.dumps(row, ensure_ascii=False))


# ---------------- B1：_ollama 单次响应 ----------------
B1_PROMPTS = [
    [{"role": "user", "content":
      "用一句话总结：产后妈妈在月子中心最关心哪三件事。只输出 JSON："
      "{\"top3\":[\"...\",\"...\",\"...\"]}"}],
    [{"role": "user", "content":
      "把下面这句话改成更口语的抖音开场白，20字内：产后腰疼不是小事。只输出 JSON："
      "{\"line\":\"...\"}"}],
    [{"role": "user", "content":
      "列举 2 个适合月子中心短视频的钩子标题。只输出 JSON：{\"hooks\":[\"...\",\"...\"]}"}],
]


def bench_b1(rounds: int = 3) -> None:
    from aicut import llm
    # 预热一次（模型冷加载很慢，不计入统计）
    llm.chat(B1_PROMPTS[0], provider="ollama", json_mode=True, timeout=300)
    times: list[float] = []
    ok = 0
    for i in range(rounds):
        msgs = B1_PROMPTS[i % len(B1_PROMPTS)]
        t0 = time.perf_counter()
        try:
            text = llm.chat(msgs, provider="ollama", json_mode=True, timeout=300)
            dt = time.perf_counter() - t0
            times.append(dt)
            ok += 1
            parsed = llm.extract_json(text)
            print(f"  run{i+1}: {dt:.2f}s  keys={list(parsed)[:3]}  len={len(text)}")
        except Exception as exc:  # noqa: BLE001
            dt = time.perf_counter() - t0
            print(f"  run{i+1}: {dt:.2f}s ERROR {type(exc).__name__} {exc}")
    avg = statistics.mean(times) if times else float("nan")
    _rec("b1", {"rounds": rounds, "ok": ok,
                "times_s": [round(x, 2) for x in times],
                "avg_s": round(avg, 2)})


# ---------------- B6/B7/B8：shotprof.build ----------------
def bench_shot(force: bool = True) -> None:
    from aicut import llm, shotprof
    llm.CALL_LOG.clear()
    t0 = time.perf_counter()
    result = shotprof.build(MATERIAL, max_shots=8, use_cloud=False, force=force,
                            progress=lambda *a, **k: None)
    wall = time.perf_counter() - t0
    vlm_calls = len([c for c in llm.CALL_LOG if c.get("ok")])
    vlm_fail = len([c for c in llm.CALL_LOG if not c.get("ok")])
    shots = result.get("shots", [])
    sizes = [s.get("shot_size") for s in shots]
    angles = [s.get("shot_angle") for s in shots]
    motions = [s.get("shot_motion") for s in shots]
    contents = [len(s.get("content", "")) for s in shots]
    print(f"  wall={wall:.2f}s  shots={len(shots)}  vlm_ok={vlm_calls} vlm_fail={vlm_fail}")
    print(f"  shot_size={sizes}")
    print(f"  shot_angle={angles}")
    print(f"  shot_motion={motions}")
    print(f"  content_len={contents}")
    print(f"  skipped_similar={[i for i,s in enumerate(shots) if s.get('_similar')]}")
    _rec("shot", {"force": force, "wall_s": round(wall, 2), "shots": len(shots),
                  "vlm_ok": vlm_calls, "vlm_fail": vlm_fail,
                  "shot_size": sizes, "shot_angle": angles, "shot_motion": motions,
                  "content_len": contents,
                  "similar_skipped": [i for i, s in enumerate(shots) if s.get("_similar")]})


# ---------------- B15：静音检测缓存 ----------------
def bench_silence() -> None:
    from aicut import skills
    cache_dir = ROOT / "work" / "cache"
    # 清掉旧缓存，模拟首次
    if cache_dir.exists():
        for f in cache_dir.glob(f"silence-{MATERIAL.stem}-*.json"):
            f.unlink()
    t0 = time.perf_counter()
    s1 = skills.detect_silences(MATERIAL, -30.0, 0.5)
    first = time.perf_counter() - t0
    t1 = time.perf_counter()
    s2 = skills.detect_silences(MATERIAL, -30.0, 0.5)
    second = time.perf_counter() - t1
    cache_files = list(cache_dir.glob(f"silence-{MATERIAL.stem}-*.json")) if cache_dir.exists() else []
    print(f"  first(no cache)={first:.3f}s  spans={len(s1)}")
    print(f"  second(cache)={second:.3f}s  spans={len(s2)}  cache_files={[f.name for f in cache_files]}")
    print(f"  spans_equal={s1 == s2}")
    _rec("silence", {"first_s": round(first, 3), "second_s": round(second, 3),
                     "speedup": round(first / second, 2) if second else None,
                     "spans_first": len(s1), "spans_second": len(s2),
                     "equal": s1 == s2, "cache_files": [f.name for f in cache_files]})
    # 参数变化失效验证
    t2 = time.perf_counter()
    s3 = skills.detect_silences(MATERIAL, -40.0, 0.8)  # 不同参数
    diffparam = time.perf_counter() - t2
    print(f"  diff-param(db=-40,min=0.8)={diffparam:.3f}s spans={len(s3)} (应重新检测)")
    cache_files2 = list(cache_dir.glob(f"silence-{MATERIAL.stem}-*.json"))
    print(f"  cache files now={len(cache_files2)} (应 >=2 个不同 key)")
    _rec("silence_diffparam", {"s": round(diffparam, 3), "spans": len(s3),
                               "cache_files": len(cache_files2)})


# ---------------- B6/B7/B8 微基准：分别归因 ----------------
def bench_micro() -> None:
    """用真实抽出的帧，分别测：B6 一次合并调用 vs 旧两步；B7 ahash 相邻距离；B8 顺序 vs 并发。"""
    import tempfile
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image
    from aicut import shotprof, llm
    v = MATERIAL
    tmp = Path(tempfile.mkdtemp(prefix="aicut-micro-"))
    frames = []
    for t in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]:
        p = tmp / f"f{t:.0f}.jpg"
        if shotprof.extract_frame(v, t, p):
            frames.append(p)
    print(f"  extracted {len(frames)} frames")

    # ---- B7：相邻帧 ahash 汉明距离 ----
    hashes = [shotprof._ahash(Image.open(f)) for f in frames]
    dists = [shotprof._hamming(hashes[i], hashes[i + 1]) for i in range(len(hashes) - 1)]
    skipped = sum(1 for d in dists if d < shotprof.HAM_SIMILAR_DIST)
    print(f"  B7 adjacent hamming dists={dists} (阈值<{shotprof.HAM_SIMILAR_DIST}) "
          f"=> 可跳过 {skipped}/{len(dists)}")

    # ---- B6：合并一次 vs 旧两步 ----
    llm.CALL_LOG.clear()
    t0 = time.perf_counter()
    u, s, sl = shotprof._understand_merged(frames[0], False)
    b6_new = time.perf_counter() - t0
    calls_new = len([c for c in llm.CALL_LOG if c.get("vision")])
    llm.CALL_LOG.clear()
    t0 = time.perf_counter()
    shotprof._understand(frames[0], False)
    shotprof._classify_three_slots(frames[0], False)
    b6_old = time.perf_counter() - t0
    calls_old = len([c for c in llm.CALL_LOG if c.get("vision")])
    print(f"  B6 merged={b6_new:.2f}s/{calls_new}calls | old2step={b6_old:.2f}s/{calls_old}calls")

    # ---- B8：6 帧顺序 vs 并发(2) ----
    llm.CALL_LOG.clear()
    t0 = time.perf_counter()
    for f in frames:
        shotprof._understand_merged(f, False)
    seq = time.perf_counter() - t0
    llm.CALL_LOG.clear()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(lambda f: shotprof._understand_merged(f, False), frames))
    conc = time.perf_counter() - t0
    print(f"  B8 sequential(6)={seq:.2f}s | concurrent-2(6)={conc:.2f}s | "
          f"speedup={seq / conc:.2f}x" if conc else f"  B8 sequential(6)={seq:.2f}s")

    _rec("micro", {"frames": len(frames), "b7_dists": dists,
                   "b7_skippable": skipped,
                   "b6_new_s": round(b6_new, 2), "b6_new_calls": calls_new,
                   "b6_old_s": round(b6_old, 2), "b6_old_calls": calls_old,
                   "b8_seq_s": round(seq, 2), "b8_conc_s": round(conc, 2),
                   "b8_speedup": round(seq / conc, 2) if conc else None})


# ---------------- B18：whisper ----------------
def bench_transcribe(video: str) -> None:
    cfg = json.loads((ROOT / "config" / "aicut.json").read_text(encoding="utf-8"))
    asr = cfg["asr"]
    from faster_whisper import WhisperModel
    device = os.environ.get("WHISPER_DEVICE", asr["device"])
    compute_type = os.environ.get("WHISPER_COMPUTE", asr["compute_type"])
    beam = int(os.environ.get("WHISPER_BEAM", asr["beam_size"]))
    print(f"  loading {asr['model']} device={device} compute={compute_type} beam={beam}")
    model = WhisperModel(asr["model"], device=device, compute_type=compute_type,
                         download_root=asr["model_dir"])
    t0 = time.perf_counter()
    segments, info = model.transcribe(
        video, language=asr["language"], beam_size=beam,
        vad_filter=asr["vad"], word_timestamps=False)
    rows = [s for s in segments if s.text.strip()]  # 触发迭代
    wall = time.perf_counter() - t0
    print(f"  transcribe wall={wall:.2f}s  duration={info.duration:.1f}s  segments={len(rows)}")
    _rec("transcribe", {"device": device, "compute_type": compute_type, "beam": beam,
                        "wall_s": round(wall, 2), "audio_dur_s": round(info.duration, 2),
                        "rtf": round(wall / max(info.duration, 0.01), 2),
                        "segments": len(rows),
                        "text_head": (rows[0].text.strip()[:40] if rows else "")})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["b1", "shot", "silence", "transcribe", "micro", "env"])
    ap.add_argument("--video", default=str(MATERIAL))
    ap.add_argument("--no-force", action="store_true")
    args = ap.parse_args()
    if args.cmd == "b1":
        bench_b1()
    elif args.cmd == "shot":
        bench_shot(force=not args.no_force)
    elif args.cmd == "silence":
        bench_silence()
    elif args.cmd == "transcribe":
        bench_transcribe(args.video)
    elif args.cmd == "micro":
        bench_micro()
    elif args.cmd == "env":
        print("material1:", MATERIAL.exists(), MATERIAL)
        print("material2:", MATERIAL2.exists(), MATERIAL2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
