"""Create a Video-RAG seed record from a local video file.

The seed is intentionally conservative: it records provenance and media facts
without inventing transcript, OCR, or visual labels. Later analyzers can merge
those fields into the same segment record.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def probe(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    cmd = [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        data = json.loads(subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {"duration": float(fmt["duration"]) if fmt.get("duration") else None,
            "width": video.get("width"), "height": video.get("height"),
            "codec": video.get("codec_name")}


def main() -> int:
    ap = argparse.ArgumentParser(description="将本地视频登记为 AIcut Video-RAG 初始记录")
    ap.add_argument("video", type=Path)
    ap.add_argument("--platform", default="local", choices=["local", "douyin", "bilibili", "youtube", "tiktok", "other"])
    ap.add_argument("--title", default=None)
    ap.add_argument("--creator", default="")
    ap.add_argument("--license-status", default="user_owned", choices=["unknown", "user_owned", "licensed", "public_domain", "reference_only"])
    ap.add_argument("--output", type=Path, default=Path("content-library/rag/video_ingest.jsonl"))
    args = ap.parse_args()
    video = args.video.resolve()
    if not video.exists():
        ap.error(f"video not found: {video}")
    facts = probe(video)
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "video_id": "local-" + video.stem,
        "platform": args.platform,
        "source": {"url": video.as_uri(), "title": args.title or video.stem,
                    "creator": args.creator, "published_at": None,
                    "license_status": args.license_status},
        "duration": facts.get("duration"),
        "media": {**facts, "path": str(video), "ingested_at": now},
        "segments": [{"segment_id": "seg-000", "start": 0.0,
                      "end": facts.get("duration") or 0.0, "transcript": "",
                      "ocr": [], "keywords": [], "objects": [], "actions": [],
                      "emotion": None, "thumbnail": None, "embedding_ref": None,
                      "analysis_status": "pending"}],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps({"video_id": record["video_id"], "output": str(args.output), "status": "seeded"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
