#!/usr/bin/env python3
r"""CLI：对单个真实视频样本做风格分析，产出与 styles.json 兼容的风格卡。

用法（在 venv 下跑）:
    python scripts\analyze_style.py --video work\style-samples\bili-3.mp4 \
        --name bili3-game --platform B站 --url https://www.bilibili.com/video/BV... \
        --out content-library\styles-analyzed.json

会把所有分析出的风格卡合并进 --out（已存在的卡按 id 覆盖），中间产物留 work/style-frames/<id>/。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import style_analyzer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="真实视频 -> 风格卡")
    ap.add_argument("--video", required=True, help="视频路径")
    ap.add_argument("--name", required=True, help="风格卡 id，如 bili3-game")
    ap.add_argument("--platform", default="B站", help="来源平台")
    ap.add_argument("--url", default="", help="来源视频链接（写进风格卡备查）")
    ap.add_argument("--out", default=str(ROOT / "content-library" / "styles-analyzed.json"),
                    help="合并输出的风格卡 JSON")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.is_absolute():
        video = ROOT / video
    if not video.exists():
        print(f"找不到视频: {video}", file=sys.stderr)
        return 1

    card = style_analyzer.analyze(video, name=args.name, platform=args.platform,
                                  source_url=args.url)

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    data: dict = {"说明": "从真实网络视频自动分析出的风格卡，字段与 styles.json 兼容。",
                  "styles": []}
    if out.exists():
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            data = {"说明": "", "styles": []}
    styles = data.setdefault("styles", [])
    styles = [s for s in styles if s.get("id") != args.name]
    styles.append(card)
    data["styles"] = styles
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n已写入 {out}（共 {len(styles)} 张分析风格卡）")
    print(f"  id={card['id']} 平台={card['来源平台']}")
    p = card["剪辑参数"]
    print(f"  镜头 {p['镜头最短秒']}-{p['镜头最长秒']}s | 转场={p['转场调用名']} | 滤镜={p['滤镜']}({p['滤镜强度']})")
    print(f"  主配色 {p['主配色']} | 钩子={card['钩子类型']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
