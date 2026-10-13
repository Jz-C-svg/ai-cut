#!/usr/bin/env python3
r"""B-roll 索引：把素材库里的镜头切出来并让视觉模型描述，形成可检索的画面清单。"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import analyze_media  # noqa: E402
import understand  # noqa: E402

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
FRAME_DIR = ROOT / "work" / "broll_frames"


def list_videos(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(f for f in target.rglob("*") if f.suffix.lower() in VIDEO_EXT)
    return [target] if target.suffix.lower() in VIDEO_EXT else []


def shots_of(video: Path, max_per_file: int) -> list[list[float]]:
    total = analyze_media.duration_of(video)
    cuts = analyze_media.detect_scenes(video, 0.3)
    bounds = [0.0] + cuts + [total]
    shots = [[bounds[i], bounds[i + 1]] for i in range(len(bounds) - 1) if bounds[i + 1] - bounds[i] > 0.6]
    if not shots:
        n = max(1, min(max_per_file, int(total // 2) or 1))
        step = total / n
        shots = [[i * step, min((i + 1) * step, total)] for i in range(n)]
    return shots[:max_per_file]


def describe(video: Path, at: float, index: int, key: str) -> dict:
    FRAME_DIR.mkdir(parents=True, exist_ok=True)
    frame = FRAME_DIR / f"{video.stem}_{index}.jpg"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(at), "-i", str(video),
                    "-frames:v", "1", "-vf", "scale=960:-1", str(frame)], capture_output=True)
    if not frame.exists():
        return {}
    img = base64.b64encode(frame.read_bytes()).decode()
    prompt = ("用一句中文描述这个画面：主体、场景、可辨认的物体或动作。再给一个类型标签，"
              '例如 人物特写/室内环境/产品细节/空间全景/动作过程。只输出 JSON：{"描述": "...", "类型": "..."}')
    try:
        text = understand.vision_cloud(img, key, prompt) if key else understand.vision_local(img, prompt)
        data = understand.extract_json(text) or {}
        desc = str(data.get("描述") or "").strip()
        if not desc:
            desc = re.sub(r"^```.*?```$", "", text, flags=re.S).strip().replace("\n", " ")[:220]
        kind = str(data.get("类型") or "").strip()
        if not kind:
            for kw, label in (("女性", "人物特写"), ("男性", "人物特写"), ("桌", "室内环境"), ("房间", "空间全景"),
                              ("窗", "室内环境"), ("手", "动作过程"), ("产品", "产品细节"), ("床", "室内环境")):
                if kw in desc:
                    kind = label
                    break
    except Exception as exc:
        desc, kind = f"(识别失败 {type(exc).__name__})", ""
    return {"描述": desc, "类型": kind}

def main() -> int:
    ap = argparse.ArgumentParser(description="B-roll 索引")
    ap.add_argument("--dir", required=True, help="素材文件或目录")
    ap.add_argument("--max-per-file", type=int, default=3)
    ap.add_argument("--max-total", type=int, default=12)
    ap.add_argument("--out", default="work/broll-index.json")
    args = ap.parse_args()

    target = Path(args.dir)
    if not target.is_absolute():
        target = ROOT / target
    if not target.exists():
        print(f"找不到素材: {target}", file=sys.stderr)
        return 1

    key = understand.load_key()
    if not key:
        print("[提示] 没有 DEEPSEEK_API_KEY，将使用本地视觉模型（较慢）")

    videos = list_videos(target)
    if not videos:
        print("目录里没有视频", file=sys.stderr)
        return 1

    index, idx = [], 0
    for video in videos:
        if idx >= args.max_total:
            break
        try:
            shots = shots_of(video, args.max_per_file)
        except Exception as exc:
            print(f"[跳过] {video.name}: {type(exc).__name__}")
            continue
        for start, end in shots:
            if idx >= args.max_total:
                break
            idx += 1
            info = describe(video, (start + end) / 2, idx, key)
            item = {"id": f"B{idx:02d}", "file": str(video), "start": round(start, 2),
                    "end": round(end, 2), "时长": round(end - start, 2), **info}
            index.append(item)
            print(f"  {item['id']} {video.name} {item['start']}-{item['end']}s [{item['类型']}] {item['描述'][:46]}")

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"素材": str(target), "镜头": index}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n共索引 {len(index)} 个镜头，已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
