#!/usr/bin/env python3
r"""标定识别灵敏度：拿真实素材算出"哪一档阈值最合适"，而不是凭感觉调数字。

做的事：
  --compare   同一素材跑 high/medium/low 三档，列出镜头数、糊镜头、清晰镜头、
              主体最清晰镜头、停顿数与停顿总秒数（不改配置）
  --sweep     横扫模糊灵敏度 × 场景阈值，看识别结果如何随之变化
  --preset X --write   把某档写进 config/perception.json（会备份 .bak）
  --show      看当前配置

用法:
  python tune_perception.py --video "work\normalized\月子知识-0310.mp4" --compare
  python tune_perception.py --video x.mp4 --preset high --write
  python tune_perception.py --video x.mp4 --sweep --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli, perception, skills, vision  # noqa: E402

cli.setup()


def measure(video: Path, *, use_cache: bool = False) -> dict:
    """按当前 perception 配置算一遍，返回可比较的指标。"""
    stream = vision.analyze(video, fps=float(perception.get("sample_fps")), use_cache=use_cache)
    shots = stream["shots"]
    sl = skills.detect_silences(video, float(perception.get("silence_db")),
                                float(perception.get("silence_min_len")))
    facey = max(shots, key=lambda s: s["face_score"], default=None)
    return {
        "镜头数": len(shots),
        "糊镜头": sum(1 for s in shots if s["grade"] == "糊"),
        "清晰镜头": sum(1 for s in shots if s["grade"] == "清晰"),
        "过曝镜头": sum(1 for s in shots if s["exposure"] == "过曝"),
        "运动大镜头": sum(1 for s in shots if s["motion"] >= float(perception.get("motion_lively"))),
        "主体最清晰": (facey or {}).get("i"),
        "主体分": (facey or {}).get("face_score"),
        "停顿数": len(sl),
        "停顿总秒": round(sum(b - a for a, b in sl), 2),
        "_shots": shots,
    }


def strip(metrics: dict) -> dict:
    return {k: v for k, v in metrics.items() if not k.startswith("_")}


def main() -> int:
    ap = argparse.ArgumentParser(description="识别灵敏度标定")
    ap.add_argument("--video", required=True, help="素材路径（用真实素材才准）")
    ap.add_argument("--compare", action="store_true", help="比较三档预设")
    ap.add_argument("--sweep", action="store_true", help="横扫模糊灵敏度 × 场景阈值")
    ap.add_argument("--preset", choices=list(perception.PRESETS), help="要采用的档位")
    ap.add_argument("--write", action="store_true", help="把 --preset 写进配置")
    ap.add_argument("--show", action="store_true", help="打印当前配置")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.show or (not args.compare and not args.sweep and not args.preset):
        cli.print_json(perception.load()) if args.json else print(
            json.dumps(perception.load(), ensure_ascii=False, indent=2))
        return 0

    video = Path(args.video)
    if not video.exists():
        raise SystemExit(f"找不到素材：{video}")
    original = perception.load()
    out: dict = {"video": str(video), "原始配置档位": original.get("preset")}

    if args.preset and args.write:
        perception.apply_preset(args.preset)
        path = perception.save(note=f"由 tune_perception.py 采用 {args.preset} 档")
        print(f"已采用 {args.preset} 档并写入 {path}（旧配置备份为 .bak）")
        if not args.compare and not args.sweep:
            return 0

    if args.compare:
        rows = {}
        for name in ("high", "medium", "low"):
            perception.apply_preset(name)
            rows[name] = strip(measure(video))
        out["三档对比"] = rows
        if not args.json:
            print(f"素材 {video.name}（越敏感越容易判糊、越容易抓到停顿）")
            print(f"{'档位':<8}{'镜头':>5}{'糊':>5}{'清晰':>6}{'过曝':>6}{'运动大':>7}{'停顿数':>7}{'停顿秒':>8}")
            for name, r in rows.items():
                print(f"{name:<8}{r['镜头数']:>5}{r['糊镜头']:>5}{r['清晰镜头']:>6}"
                      f"{r['过曝镜头']:>6}{r['运动大镜头']:>7}{r['停顿数']:>7}{r['停顿总秒']:>8}")

    if args.sweep:
        perception.apply_preset("medium")
        base = perception.get("scene_cut")
        table = []
        for sens in (0.8, 1.0, 1.25, 1.5):
            perception.set_value("blur_sensitivity", sens)
            for cut in (round(base - 0.1, 2), base, round(base + 0.1, 2)):
                perception.set_value("scene_cut", cut)
                rec = strip(measure(video))
                rec.update({"模糊灵敏度": sens, "场景阈值": cut})
                table.append(rec)
        out["扫描"] = table
        if not args.json:
            print(f"\n{'模糊灵敏度':>10}{'场景阈值':>9}{'镜头':>5}{'糊':>5}{'清晰':>6}{'停顿秒':>8}")
            for r in table:
                print(f"{r['模糊灵敏度']:>10}{r['场景阈值']:>9}{r['镜头数']:>5}"
                      f"{r['糊镜头']:>5}{r['清晰镜头']:>6}{r['停顿总秒']:>8}")

    perception._CACHE = original  # noqa: SLF001  还原内存配置
    if args.json:
        cli.print_json(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
