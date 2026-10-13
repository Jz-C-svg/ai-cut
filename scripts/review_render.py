#!/usr/bin/env python3
"""Post-render two-pass frame review for decoration cleanliness and final composition.

The automated score is a deterministic cleanliness signal, not an aesthetic verdict.
Each pass saves evidence frames and a contact sheet for human review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import subprocess
import uuid
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))


def duration_of(video: Path) -> float:
    proc = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(video),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=True)
    return float(proc.stdout.strip())


def read_sheet(path: Path | None) -> dict:
    if not path:
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _unique(values: list[float], duration: float, gap: float = .18) -> list[float]:
    out: list[float] = []
    for raw in sorted(max(0.02, min(duration - .02, float(x))) for x in values if duration > .04):
        if not out or raw - out[-1] >= gap:
            out.append(round(raw, 3))
    return out


def decoration_times(sheet: dict, duration: float) -> list[float]:
    anchors: list[tuple[float, float]] = []
    for collection, start_key, end_key in ((sheet.get("visual_plan") or [], "t0", "t1"),
                                            (sheet.get("kinetic_text") or [], "start", "end")):
        for item in collection:
            try:
                start, end = float(item[start_key]), float(item[end_key])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= start < end <= duration + .1:
                anchors.append((start, end))
    for i, clip in enumerate(sheet.get("clips") or []):
        if clip.get("特效") or clip.get("转场调用名"):
            # Clip starts are source times; use the target timeline offset below.
            at = sum((float(c["end"]) - float(c["start"])) / float(c.get("speed", 1))
                     for c in (sheet.get("clips") or [])[:i])
            length = (float(clip["end"]) - float(clip["start"])) / float(clip.get("speed", 1))
            anchors.append((at, at + length))
    if not anchors:
        # Even without stickers or transitions, subtitles change the rendered picture.
        sub = sheet.get("subtitles_json")
        if sub:
            path = Path(sub)
            if not path.is_absolute():
                path = ROOT / path
            if path.exists():
                try:
                    for seg in json.loads(path.read_text(encoding="utf-8-sig")).get("segments", []):
                        anchors.append((float(seg["start"]), float(seg["end"])))
                except Exception:
                    pass
    points: list[float] = []
    for start, end in anchors:
        points.extend((start + min(.25, (end - start) / 3), (start + end) / 2,
                       end - min(.2, (end - start) / 4)))
    if not points and duration:
        points = [duration * .25, duration * .5, duration * .75]
    return _unique(points, duration)[:48]


def final_review_times(video: Path, duration: float, count: int = 12) -> list[float]:
    st = video.stat()
    seed = int(hashlib.sha256(f"{video.resolve()}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    if duration <= .2:
        return [duration / 2]
    gap = max(.15, duration / (count * 2))
    values = _unique([.05, duration / 2, duration - .05], duration, gap=max(.02, gap / 2))
    attempts = 0
    while len(values) < min(count, max(1, int(duration * 2))) and attempts < count * 20:
        attempts += 1
        candidate = rng.uniform(.05, max(.06, duration - .05))
        if all(abs(candidate - old) >= gap for old in values):
            values = _unique(values + [candidate], duration, gap=max(.02, gap / 2))
    if len(values) < min(count, max(1, int(duration * 2))):
        values = _unique(values + [duration * i / count for i in range(1, count)], duration, gap=max(.02, gap / 2))
    return values[:count]


def sample_pass(video: Path, times: list[float], folder: Path, pass_name: str) -> list[dict]:
    from aicut.cleancheck import check_frame

    folder.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"无法读取导出视频：{video}")
    rows = []
    try:
        for i, t in enumerate(times, 1):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                rows.append({"time": t, "status": "抽帧失败"})
                continue
            path = folder / f"{i:02d}-{t:.2f}s.jpg"
            # Pillow handles Unicode Windows paths more reliably than cv2.imwrite.
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            Image.fromarray(rgb).save(path, format="JPEG", quality=92)
            metrics = check_frame(frame)
            rows.append({"time": round(t, 3), "status": "已检查", "frame": str(path),
                         "clean_score": metrics["clean_score"], "level": metrics["level"],
                         "dimensions": metrics["dims"], "issues": metrics["issues"],
                         "advice": metrics["advice"]})
    finally:
        cap.release()
    valid = [x for x in rows if x.get("status") == "已检查"]
    sheet = folder / f"{pass_name}-contact-sheet.jpg"
    if valid:
        _contact_sheet(valid, sheet)
    return rows


def _contact_sheet(rows: list[dict], output: Path) -> None:
    thumbs, cell_w, cell_h = [], 280, 540
    font_path = Path(r"C:\Windows\Fonts\msyh.ttc")
    try:
        font = ImageFont.truetype(str(font_path), 19) if font_path.exists() else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    for row in rows:
        with Image.open(row["frame"]) as src:
            im = src.convert("RGB")
            im.thumbnail((cell_w - 12, cell_h - 65))
            card = Image.new("RGB", (cell_w, cell_h), "white")
            x = (cell_w - im.width) // 2
            card.paste(im, (x, 4))
            draw = ImageDraw.Draw(card)
            draw.text((8, cell_h - 54), f"{row['time']:.2f}s  clean {row['clean_score']}  {row['level']}",
                      fill=(20, 20, 20), font=font)
            issues = ", ".join(row.get("issues") or [])[:32]
            draw.text((8, cell_h - 28), issues, fill=(150, 20, 20), font=font)
            thumbs.append(card)
    cols = min(4, max(1, len(thumbs)))
    rows_n = (len(thumbs) + cols - 1) // cols
    canvas = Image.new("RGB", (cell_w * cols, cell_h * rows_n), (230, 230, 230))
    for i, im in enumerate(thumbs):
        canvas.paste(im, ((i % cols) * cell_w, (i // cols) * cell_h))
    canvas.save(output, quality=92)


def review_video(video: Path, sheet_path: Path | None = None) -> Path:
    if not video.is_file():
        raise FileNotFoundError(video)
    duration = duration_of(video)
    sheet = read_sheet(sheet_path)
    # Each run gets fresh frame filenames: OpenCV's imwrite can fail to overwrite
    # an existing Unicode-path JPEG on Windows, which used to mark valid samples
    # as failed on every subsequent review.
    root = ROOT / "work" / "reviews" / video.stem / uuid.uuid4().hex[:10]
    effects = sample_pass(video, decoration_times(sheet, duration), root / "01-decoration", "effect-pass")
    final = sample_pass(video, final_review_times(video, duration), root / "02-final", "final-pass")
    passes = []
    for name, rows in (("effects_and_stickers_second_sample", effects), ("randomized_final_review", final)):
        valid = [x for x in rows if x.get("status") == "已检查"]
        passes.append({"name": name, "frames_checked": len(valid),
                       "mean_clean_score": round(sum(x["clean_score"] for x in valid) / len(valid), 1) if valid else None,
                       "below_75": [x for x in valid if x["clean_score"] < 75],
                       "contact_sheet": str((root / ("01-decoration" if name.startswith("effects") else "02-final") /
                                                ("effect-pass-contact-sheet.jpg" if name.startswith("effects") else "final-pass-contact-sheet.jpg"))) if valid else "",
                       "frames": rows})
    result = {"schema_version": "render-review-1", "video": str(video.resolve()),
              "duration": round(duration, 3), "sheet": str(sheet_path.resolve()) if sheet_path else None,
              "passes": passes,
              "aesthetic_review": {"status": "human_review_required",
                                   "reason": "cleancheck scores clutter/clarity/composition signals; it cannot certify visual beauty or editorial intent"},
              "limitations": ["This report reviews the rendered MP4 only.",
                              "Scores are automated image-quality cues, not human approval."]}
    report = ROOT / "work" / "reviews" / video.stem / "post-render-review.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="导出后双轮抽帧复审")
    ap.add_argument("video")
    ap.add_argument("--sheet", help="对应剪辑决策单，用于在花字/特效时间点抽查")
    args = ap.parse_args()
    video = Path(args.video)
    if not video.is_absolute():
        video = ROOT / video
    sheet = Path(args.sheet) if args.sheet else None
    if sheet and not sheet.is_absolute():
        sheet = ROOT / sheet
    report = review_video(video, sheet)
    print(f"[导出后复审] 报告 {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
