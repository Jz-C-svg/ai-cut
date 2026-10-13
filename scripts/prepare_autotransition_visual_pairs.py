"""Decode selected AutoTransition videos and build timestamped visual evidence pairs."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "content-library/web-visuals/external-datasets/autotransition"
SAMPLES = BASE / "extracted-research"
OUT = SAMPLES / "visual-pairs.jsonl"
sys.path.insert(0, str(ROOT / "lib"))
from aicut import bundled_vision  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def probe(path: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                        "-show_entries", "stream=codec_name,width,height,avg_frame_rate,nb_read_frames",
                        "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, encoding="utf-8", check=True)
    d = json.loads(p.stdout)
    stream = (d.get("streams") or [{}])[0]
    return {"duration_seconds": float(d["format"]["duration"]), "codec": stream.get("codec_name"),
            "width": stream.get("width"), "height": stream.get("height"),
            "frame_rate": stream.get("avg_frame_rate"), "decoded_frames": int(stream.get("nb_read_frames", 0))}


def decode_all(path: Path) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
                    "-map", "0:v:0", "-f", "null", "-"], capture_output=True, check=True)


def frame(path: Path, seconds: float, output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-y",
                    "-ss", f"{seconds:.6f}", "-i", str(path), "-frames:v", "1", "-q:v", "2",
                    "-vf", "scale=960:-2", str(output)], capture_output=True, check=True)
    if not output.is_file() or output.stat().st_size < 1000:
        raise RuntimeError(f"failed to extract frame at {seconds:.3f}s from {path}")
    return {"time_seconds": round(seconds, 6), "path": output.relative_to(ROOT).as_posix(),
            "sha256": sha256(output)}


def contact_sheet(frames: list[dict], output: Path) -> dict:
    panels = []
    font = ImageFont.load_default()
    for record in frames:
        image = Image.open(ROOT / record["path"]).convert("RGB")
        image.thumbnail((640, 360))
        panel = Image.new("RGB", (640, 392), "#111827")
        panel.paste(image, ((640 - image.width) // 2, 24 + (360 - image.height) // 2))
        ImageDraw.Draw(panel).text((10, 5), f"{record['role']} t={record['time_seconds']:.3f}s", fill="white", font=font)
        panels.append(panel)
    sheet = Image.new("RGB", (640 * len(panels), 392), "#0b1020")
    for index, panel in enumerate(panels):
        sheet.paste(panel, (index * 640, 0))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, "JPEG", quality=90)
    return {"path": output.relative_to(ROOT).as_posix(), "sha256": sha256(output),
            "frame_sha256s": [frame["sha256"] for frame in frames]}


def candidates() -> list[dict]:
    manifest = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))
    by_class = {}
    for video_id, video in manifest["videos"].items():
        for event in video["events"]:
            name = event["transition_class"]
            if name not in by_class and event["event_kind"] == "effect_interval":
                by_class[name] = {"video_id": video_id, "video": video, "event": event}
    return [by_class[name] for name in sorted(by_class)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-classes", type=int, default=5)
    parser.add_argument("--no-vision", action="store_true", help="extract visual pairs without model descriptions")
    args = parser.parse_args()
    if not args.no_vision and not bundled_vision.is_available():
        raise SystemExit(json.dumps(bundled_vision.health(), ensure_ascii=False))
    existing = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[row["transition_class"]] = row
    todo = [c for c in candidates() if c["event"]["transition_class"] not in existing][:args.max_classes]
    if not todo:
        print("all selected classes already processed")
        return 0
    for item in todo:
        event = item["event"]
        video_id = item["video_id"]
        video_path = ROOT / item["video"]["path"]
        meta = probe(video_path)
        decode_all(video_path)
        start, end = event["start_ms"] / 1000.0, event["end_ms"] / 1000.0
        if start < 0 or end <= start or end > meta["duration_seconds"]:
            print(f"[invalid] {event['transition_class']} time range outside decoded video", flush=True)
            continue
        samples = [max(0.0, start - 0.12), (start + end) / 2.0,
                   min(meta["duration_seconds"] - 0.001, end + 0.12)]
        roles = ["before", "during", "after"]
        frames = []
        for role, seconds in zip(roles, samples):
            record = frame(video_path, seconds,
                           SAMPLES / video_id / "frames" / event["transition_class"] / f"{role}.jpg")
            record["role"] = role
            frames.append(record)
        sheet = contact_sheet(frames, SAMPLES / video_id / "frames" / event["transition_class"] / "sequence.jpg")
        sequence_observation = None
        elapsed = None
        if not args.no_vision:
            prompt = ("Analyze this three-panel sequence of frames from one short video. The panels are labeled "
                      "before, during, and after with exact timestamps. Describe only visible changes across the "
                      "sequence: scene change, geometry, direction, blur, wipe, color/brightness, or overlay. "
                      "Do not infer a named transition class from outside metadata and do not invent motion. "
                      "Say whether any transition effect is visually supported or the label is uncertain. "
                      "Return only one valid JSON object with short string fields: scene_before, visible_change, "
                      "scene_after, effect_evidence, label_support, uncertainty.")
            t0 = time.perf_counter()
            try:
                sequence_observation = bundled_vision.analyze_frame(ROOT / sheet["path"], prompt=prompt,
                                                                     max_tokens=220, temperature=0.1, timeout=300)
            except Exception as exc:
                sequence_observation = {"error": f"{type(exc).__name__}: {exc}"}
            elapsed = round(time.perf_counter() - t0, 3)
        row = {"video_id": video_id, "source_url": event["source_url"],
               "transition_class": event["transition_class"], "event_index": event["event_index"],
               "start_ms": event["start_ms"], "end_ms": event["end_ms"],
               "video_path": item["video"]["path"], "video_sha256": item["video"]["sha256"],
               "video_metadata": meta, "frames": frames, "sequence_contact_sheet": sheet,
               "sequence_observation": sequence_observation, "analysis_seconds": elapsed,
               "vision_model": bundled_vision.info()["id"] if not args.no_vision else None,
               "review_status": "automatic_analysis_not_human_reviewed",
               "usage_scope": "research_only_source_video_rights_unverified",
               "ai_rag_eligible": False, "production_use_allowed": False,
               "evidence_status": "video_full_decode_frames_timecodes_and_sequence_observation_paired" if sequence_observation and not sequence_observation.get("error") else "video_full_decode_and_frames_paired_model_observation_incomplete",
               "limitations": ["source-level video rights not independently verified", "one event exemplar per class", "model description requires human visual review"]}
        existing[row["transition_class"]] = row
        OUT.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                                             for _, r in sorted(existing.items())), encoding="utf-8")
        print(f"[paired] {row['transition_class']} {video_id} {len(frames)} frames", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
