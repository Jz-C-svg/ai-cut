"""Extract periodic keyframes and attach them to a Video-RAG record."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("record", type=Path)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--frames-dir", type=Path, default=Path("work/rag-keyframes"))
    args = ap.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        ap.error("ffmpeg is required")
    args.frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = args.frames_dir / (args.video.stem + "_%05d.jpg")
    cmd = [ffmpeg, "-y", "-i", str(args.video), "-vf", f"fps=1/{args.interval}", "-q:v", "3", str(pattern)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frames = sorted(args.frames_dir.glob(args.video.stem + "_*.jpg"))
    data = [json.loads(x) for x in args.record.read_text(encoding="utf-8").splitlines() if x.strip()]
    for item in data:
        for seg in item.get("segments", []):
            midpoint = (float(seg.get("start", 0)) + float(seg.get("end", 0))) / 2
            idx = max(0, int(midpoint // args.interval))
            if idx < len(frames):
                seg["thumbnail"] = str(frames[idx].resolve())
                seg["keyframe_status"] = "extracted"
    out = args.output or args.record
    out.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in data) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "frames": len(frames), "interval": args.interval, "status": "keyframes_attached"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
