"""视觉模型管理：列出 / 切换 / benchmark 本地 Ollama 视觉模型（v3.9.0）。

用法：
    python model_manager.py list                 列出 ollama 中的视觉模型，标注当前默认
    python model_manager.py switch <模型名>      切换默认视觉模型（写入 config/aicut.json）
    python model_manager.py benchmark [--videos 3]
                                                 用 3 个口播素材分别跑 gemma4 / qwen2.5vl，
                                                 对比字段完整率 / 单镜头耗时 / 显存占用 / 三槽位一致性

环境变量 AICUT_VISION_MODEL 若设置，则优先级高于 config（临时切换用）。
注意：lib/aicut/paths.py 的 config() 有 lru_cache，switch 后已运行中的进程需重启才生效。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from aicut.ollama_endpoint import ollama_url, ollama_base_url

CONFIG = ROOT / "config" / "aicut.json"
# 识别"视觉模型"用的名字关键词（gemma / qwen / vl / vision）
VISION_KEYWORDS = ("vl", "vision", "gemma", "qwen")
# benchmark 默认素材（母婴口播）
BENCH_VIDEOS = ["IMG_5840.mp4", "IMG_5842.mp4", "月子知识-0299.mp4"]
BENCH_DIR = ROOT / "work" / "normalized"


def _load_cfg() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}


def _save_cfg(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _ollama_tags() -> list[dict]:
    try:
        with urllib.request.urlopen(ollama_url('/api/tags'), timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8")).get("models", [])
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 连不上 ollama（{type(exc).__name__}），确认 `ollama serve` 已启动。")
        return []


def _model_installed(name: str, installed: set[str]) -> bool:
    """灵活匹配模型名：ollama 中 'foo:latest' 与配置里的 'foo' 视为同一个。"""
    if name in installed:
        return True
    return (name + ":latest") in installed


def _ollama_ps() -> str:
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10,
                             env={**os.environ, 'OLLAMA_HOST': ollama_base_url()})
        return (out.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def cmd_list() -> int:
    from aicut import llm
    tags = _ollama_tags()
    if not tags:
        return 1
    default = llm.get_vision_model()
    print(f"当前默认视觉模型：{default}")
    print(f"{'模型名':<32} {'大小':<10} {'默认?'}")
    print("-" * 60)
    found = False
    for m in tags:
        name = m.get("name", "")
        if not any(k in name.lower() for k in VISION_KEYWORDS):
            continue
        found = True
        size = m.get("size", 0)
        size_s = f"{size / 1e9:.1f} GB" if isinstance(size, (int, float)) else "?"
        # ollama 列表名带 :latest tag，默认值可能不带 tag，做归一化比较
        base = name.split(":")[0]
        mark = "  <== 默认" if (name == default or base == default or default.startswith(base + ":")) else ""
        print(f"{name:<32} {size_s:<10}{mark}")
    if not found:
        print("（ollama 中没有匹配视觉模型关键词的模型）")
    print("\n别名登记（lib/aicut/llm.py VISION_MODELS）：")
    for alias, real in llm.VISION_MODELS.items():
        print(f"  {alias:<10} -> {real}")
    return 0


def cmd_switch(model: str) -> int:
    from aicut import llm
    # 支持别名解析（gemma4 / qwen3.5），也允许直接给完整模型名
    target = llm.VISION_MODELS.get(model, model)
    installed = [m.get("name", "") for m in _ollama_tags()]
    if installed and not _model_installed(target, set(installed)):
        print(f"[警告] ollama 中未找到 '{target}'（已安装：{', '.join(installed) or '无'}）。")
        if input("仍要写入配置？[y/N] ").strip().lower() != "y":
            print("已取消。")
            return 1
    cfg = _load_cfg()
    cfg.setdefault("vision", {})["model"] = target
    _save_cfg(cfg)
    print(f"[OK] 默认视觉模型已切换为：{target}")
    print(f"     写入 {CONFIG} 的 vision.model 字段。")
    print("     注意：正在运行的进程因 lru_cache 需重启才生效；AICUT_VISION_MODEL 环境变量优先级更高。")
    return 0


def cmd_benchmark(videos_n: int) -> int:
    from aicut import llm, shotprof
    tags = _ollama_tags()
    installed = {m.get("name", "") for m in tags}
    candidates = list(dict.fromkeys([
        llm.VISION_MODELS["gemma4"],
        llm.VISION_MODELS.get("qwen3.5") or llm.OLLAMA_MODEL,
    ]))
    models = [m for m in candidates if _model_installed(m, installed)]
    missing = [m for m in candidates if not _model_installed(m, installed)]
    if missing:
        print(f"[跳过] 未安装、不参与 benchmark：{', '.join(missing)}")
    if not models:
        print("[错误] 没有可用的视觉模型，先 `ollama pull`。")
        return 1

    vids = [BENCH_DIR / v for v in BENCH_VIDEOS[:videos_n]]
    vids = [v for v in vids if v.exists()]
    if not vids:
        print(f"[错误] benchmark 素材目录没有找到 {BENCH_VIDEOS}（{BENCH_DIR}）")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="aicut-bench-"))
    results: dict[str, list[dict]] = {m: [] for m in models}
    try:
        for v in vids:
            mid = v.stat().st_size and 5.0  # 抽第 5 秒一帧即可（口播机位图稳定）
            frame = tmp / f"{v.stem}.jpg"
            if not shotprof.extract_frame(v, mid, frame):
                print(f"[跳过] 抽帧失败：{v.name}")
                continue
            for m in models:
                t0 = time.time()
                rec: dict = {"video": v.name, "model": m}
                try:
                    raw = llm.vision_json(frame, shotprof.MERGED_PROMPT,
                                          provider="ollama", model=m, timeout=300)
                    rec["seconds"] = round(time.time() - t0, 2)
                    got = sum(1 for k in shotprof.MERGED_FIELDS
                              if shotprof._field_present(raw.get(k)))
                    rec["completeness"] = round(got / len(shotprof.MERGED_FIELDS), 3)
                    rec["slots"] = {k: raw.get(k) for k in
                                    ("shot_size", "shot_angle", "shot_motion", "emotion_tag")}
                    rec["content"] = str(raw.get("content", ""))[:30]
                except Exception as exc:  # noqa: BLE001
                    rec["seconds"] = round(time.time() - t0, 2)
                    rec["error"] = f"{type(exc).__name__}: {exc}"[:120]
                results[m].append(rec)
                print(f"  {v.name:<22} {m:<22} {rec.get('seconds')}s "
                      f"完整率 {rec.get('completeness', '-')} "
                      f"{rec.get('error', '')}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 对比表 ----
    print("\n" + "=" * 78)
    print(f"{'模型':<24} {'平均耗时(s)':<12} {'平均字段完整率':<14} {'错误数'}")
    print("-" * 78)
    for m in models:
        rs = [r for r in results[m] if "error" not in r]
        avg_s = round(sum(r["seconds"] for r in rs) / len(rs), 2) if rs else "-"
        avg_c = round(sum(r["completeness"] for r in rs) / len(rs), 3) if rs else "-"
        errs = sum(1 for r in results[m] if "error" in r)
        print(f"{m:<24} {str(avg_s):<12} {str(avg_c):<14} {errs}")

    # ---- 三槽位一致性：同帧两模型分类是否一致 ----
    if len(models) == 2:
        a, b = models
        pairs = {r["video"]: r for r in results[a]}, {r["video"]: r for r in results[b]}
        common = set(pairs[0]) & set(pairs[1])
        if common:
            agree = 0
            for vid in sorted(common):
                sa, sb = pairs[0][vid].get("slots", {}), pairs[1][vid].get("slots", {})
                keys = ("shot_size", "shot_angle", "shot_motion")
                same = sum(1 for k in keys if sa.get(k) == sb.get(k))
                agree += same / len(keys)
                print(f"  一致性 {vid:<22}: {same}/3 槽位一致  "
                      f"gemma={tuple(sa.get(k) for k in keys)}  qwen={tuple(sb.get(k) for k in keys)}")
            print(f"三槽位平均一致率：{round(agree / len(common), 3)}（{len(common)} 个素材）")

    # ---- 显存占用（ollama ps） ----
    ps = _ollama_ps()
    if ps:
        print("\n[ollama ps · 当前驻留显存]")
        print(ps)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="AI cut 视觉模型管理（list / switch / benchmark）")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="列出 ollama 中的视觉模型并标注默认")
    sp = sub.add_parser("switch", help="切换默认视觉模型")
    sp.add_argument("model", help="模型别名（gemma4/qwen2.5vl）或 ollama 模型名")
    bp = sub.add_parser("benchmark", help="对比 gemma4 与 qwen2.5vl")
    bp.add_argument("--videos", type=int, default=3, help="用前 N 个素材（默认 3）")
    args = ap.parse_args()

    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "switch":
        return cmd_switch(args.model)
    if args.cmd == "benchmark":
        return cmd_benchmark(args.videos)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
