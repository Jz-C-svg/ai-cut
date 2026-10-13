"""Detect scene boundaries with FFmpeg and write shot segments to Video-RAG."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("record", type=Path)
    ap.add_argument("--threshold", type=float, default=0.35)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        ap.error("ffmpeg is required")
    proc = subprocess.run([ffmpeg, "-hide_banner", "-i", str(args.video), "-vf", f"select='gt(scene,{args.threshold})',showinfo", "-f", "null", "-"], capture_output=True, text=True)
    cuts = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]
    items = [json.loads(x) for x in args.record.read_text(encoding="utf-8").splitlines() if x.strip()]
    for item in items:
        duration = float(item.get("duration") or 0)
        boundaries = [0.0] + sorted(set(x for x in cuts if 0 < x < duration)) + ([duration] if duration else [])
        if len(boundaries) < 2:
            continue
        base = item.get("segments", [{}])[0]
        segments = []
        for idx, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            seg = {"segment_id": f"seg-{idx:03d}", "start": round(start, 3), "end": round(end, 3), "transcript": "", "ocr": [], "keywords": [], "objects": [], "actions": [], "emotion": None, "thumbnail": None, "embedding_ref": None, "analysis_status": "scene_detected"}
            if idx == 0 and base.get("transcript"): seg["transcript"] = base["transcript"]
            segments.append(seg)
        item["segments"] = segments
        item["scene_detection"] = {"method": "ffmpeg_select_scene", "threshold": args.threshold, "cuts": len(cuts)}
    out = args.output or args.record
    out.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "cuts": len(cuts), "status": "scenes_detected"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
