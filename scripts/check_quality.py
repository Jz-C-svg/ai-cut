# -*- coding: utf-8 -*-
"""一键画面质量检查命令（check_quality）

用法：
  python scripts/check_quality.py <图片路径>                      # 单图干净度评分
  python scripts/check_quality.py <视频路径> [--at 2.5]           # 视频抽帧评分
  python scripts/check_quality.py <视频路径> --compare <成片图>    # 顺带 before/after 比对
  python scripts/check_quality.py <图> --json                      # 只输出 JSON

输出：干净度 0-100 + 五维分 + 问题/建议；--compare 生成并排比对图到 reports/。
退出码：干净度 < 75 → 1（警告），≥ 75 → 0。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import cli  # noqa: E402
from aicut import cleancheck, compare  # noqa: E402
from aicut.paths import REPORTS  # noqa: E402

cli.setup()

EXIT_WARN = 1


def _pick_frame(video: Path, at: float):
    """视频抽一帧（BGR ndarray）"""
    import cv2
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError(f"视频无法打开: {video}")
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, at) * 1000.0)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise ValueError(f"抽帧失败: {video} @ {at}s")
    return frame


def main() -> int:
    ap = argparse.ArgumentParser(description="一键画面质量检查（cleancheck + before/after 比对）")
    ap.add_argument("target", help="图片或视频路径")
    ap.add_argument("--at", type=float, default=1.0, help="视频抽帧秒数（默认 1.0s）")
    ap.add_argument("--compare", metavar="IMG", help="成片图路径，做 before/after 比对")
    ap.add_argument("--out", default=str(REPORTS), help="比对图输出目录（默认 reports/）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    target = Path(args.target)
    if not target.exists():
        print(f"❌ 文件不存在: {target}")
        return 2

    # 取帧：图片直接用；视频抽帧
    suffix = target.suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        import cv2
        frame = cv2.imread(str(target))
        if frame is None:
            print(f"❌ 图片读取失败: {target}")
            return 2
        clean = cleancheck.check_frame(frame)
        src_desc = f"图片 {target.name}"
    else:
        frame = _pick_frame(target, args.at)
        clean = cleancheck.check_frame(frame)
        src_desc = f"视频 {target.name} @ {args.at}s"

    result = {"source": str(target), "desc": src_desc, "cleancheck": clean}

    # 可选：before/after 比对
    if args.compare:
        cimg = Path(args.compare)
        if not cimg.exists():
            print(f"❌ 比对图不存在: {cimg}")
            return 2
        import cv2
        after = cv2.imread(str(cimg))
        if after is None:
            print(f"❌ 成片图读取失败: {cimg}")
            return 2
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"check_{target.stem}.png"
        cmp = compare.compare_frames(frame, after, out_path=str(out_path),
                                     label_before=f"检查源 {target.stem}",
                                     label_after="成片 After")
        result["compare"] = cmp

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        score = clean["clean_score"]
        level = clean["level"]
        dims = clean["dims"]
        print(f"\n📊 画面干净度：{score} / 100（{level}） ｜ 来源：{src_desc}")
        print(f"   五维：背景{float(dims['clutter']):.1f} · 叠影{float(dims['ghost']):.1f} · "
              f"构图{float(dims['subject']):.1f} · 字幕{float(dims['subtitle']):.1f} · "
              f"异物{float(dims['corner']):.1f}")
        if clean["issues"]:
            print("   ⚠ 问题：")
            for i in clean["issues"]:
                print(f"     - {i}")
        if clean["advice"]:
            print("   💡 建议：")
            for a in clean["advice"]:
                print(f"     - {a}")
        if "compare" in result:
            c = result["compare"]
            bm, am = c["before_metrics"], c["after_metrics"]
            print(f"\n   🔄 before/after：素材 {bm['clean_score']}（{bm['clean_level']}）→ "
                  f"成片 {am['clean_score']}（{am['clean_level']}）· 差异区 {c['diff_count']} 处")
            print(f"   比对图：{c['output']}")

    return 0 if clean["clean_score"] >= 75 else EXIT_WARN


if __name__ == "__main__":
    sys.exit(main())
