#!/usr/bin/env python3
"""生成统一的视频理解档案，供 auto_edit 和主脑使用。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import video_understanding  # noqa: E402


def _path(raw: str | None, default: Path | None = None) -> Path | None:
    p = Path(raw) if raw else default
    if p and not p.is_absolute():
        p = ROOT / p
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="统一视频理解档案")
    ap.add_argument("--video", required=True)
    ap.add_argument("--transcript")
    ap.add_argument("--analysis")
    ap.add_argument("--visual")
    ap.add_argument("--ai-understanding")
    ap.add_argument("--run-signals", action="store_true", help="缺少信号结果时自动运行确定性分析")
    ap.add_argument("--out")
    args = ap.parse_args()
    video = _path(args.video)
    if not video or not video.exists():
        print(f"找不到视频: {video}", file=sys.stderr)
        return 1
    analysis = _path(args.analysis, ROOT / "work" / f"analysis-{video.stem}.json")
    visual = _path(args.visual, ROOT / "work" / f"vision-{video.stem}.json")
    transcript = _path(args.transcript, ROOT / "02-transcript" / f"{video.stem}.json")
    ai = _path(args.ai_understanding, ROOT / "work" / f"understand-{video.stem}.json")
    if args.run_signals and (not analysis or not analysis.exists()):
        subprocess.run([sys.executable, str(ROOT / "scripts" / "analyze_media.py"),
                        str(video), "--out", str(analysis)], cwd=str(ROOT), check=True)
    if args.run_signals and (not visual or not visual.exists()):
        subprocess.run([sys.executable, str(ROOT / "scripts" / "vision_stream.py"),
                        "--video", str(video), "--json", "--out", str(visual)], cwd=str(ROOT), check=True)
    out = _path(args.out, ROOT / "work" / f"video-understanding-{video.stem}.json")
    doc = video_understanding.load_and_build(video, analysis_path=analysis, visual_path=visual,
                                             transcript_path=transcript, ai_path=ai)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "out": str(out), "schema_version": doc["schema_version"],
        "shots": len(doc["evidence"]["shots"]),
        "transcript_segments": len(doc["evidence"]["transcript"]),
        "recommendations": len(doc["recommendations"]),
        "creative_readiness": doc["inference"]["creative_readiness"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
