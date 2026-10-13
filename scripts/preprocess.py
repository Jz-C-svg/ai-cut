"""素材标准化与音轨抽取（AI cut / 阶段：预处理）。

用法:
    python preprocess.py <文件或目录>... [--out-name 名称]

输出:
    work/normalized/<名称>.mp4   统一 1080x1920 / 30fps / h264 / aac
    01-audio/<名称>.wav          16kHz 单声道（无音轨的视频会跳过并标记 audio=null）
    work/manifest.json           产物清单
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))
from aicut.production import atomic_json, file_lock, source_version, read_json, project_name

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg", ".webm"}


def load_config() -> dict:
    return read_json(Path(__file__).resolve().parent.parent / 'config' / 'aicut.json')


def probe(path: Path, ffprobe: str) -> dict:
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,sample_rate,channels,sample_aspect_ratio,display_aspect_ratio",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60, check=True,
    )
    try:
        data = json.loads(proc.stdout)
    except Exception as exc:
        raise ValueError(f'无法读取素材信息: {path}') from exc
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    try:
        duration = float(data.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "duration": duration,
        "has_audio": bool(audio),
        "has_video": bool(video),
        "width": int(video.get("width", 0) or 0),
        "height": int(video.get("height", 0) or 0),
        "sample_aspect_ratio": video.get("sample_aspect_ratio"),
        "sample_rate": int(audio.get("sample_rate", 0) or 0),
        "channels": int(audio.get("channels", 0) or 0),
    }


def run(cmd: list[str]) -> None:
    # Long source clips may legitimately take hours to normalize; bound hangs without
    # imposing a short per-clip limit. The pipeline's job runner still handles cancel.
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=6 * 60 * 60)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFmpeg 预处理超过 6 小时，已停止") from exc
    if proc.returncode != 0:
        raise RuntimeError("命令失败: " + " ".join(cmd[:3]) + "...\n" + proc.stderr[-600:])


def collect_inputs(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw).resolve()
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXT)
        elif p.is_file() and p.suffix.lower() in VIDEO_EXT:
            files.append(p)
        else:
            print(f"[跳过] 不支持的格式: {p}", file=sys.stderr)
    return list(dict.fromkeys(files))


def normalize(src: Path, name: str, cfg: dict, root: Path) -> dict:
    r = cfg["render"]
    normalized = root / "work" / "normalized" / f"{name}.mp4"
    audio = root / "01-audio" / f"{name}.wav"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    audio.parent.mkdir(parents=True, exist_ok=True)
    # Staging names must be unique across assets. Per-asset locks do not protect
    # two different source paths that happen to have the same output name.
    import uuid
    token = uuid.uuid4().hex
    staged_video = normalized.with_name(normalized.stem + f".{token}.staging.mp4")
    staged_audio = audio.with_name(audio.stem + f".{token}.staging.wav")

    info = probe(src, cfg["ffprobe"])
    if not info['has_video'] or not math.isfinite(info['duration']) or info['duration'] <= 0:
        raise ValueError(f'不是有效视频: {src}')
    vf = (
        f"scale={r['width']}:{r['height']}:force_original_aspect_ratio=decrease:reset_sar=1,"
        f"pad={r['width']}:{r['height']}:(ow-iw)/2:(oh-ih)/2:color=black,fps={r['fps']},setsar=1"
    )
    try:
        cmd = [cfg["ffmpeg"], "-y", "-i", str(src), "-vf", vf,
               "-c:v", "libx264", "-preset", r["preset"], "-crf", str(r["crf"]),
               "-pix_fmt", "yuv420p"]
        if info["has_audio"]:
            cmd += ["-c:a", "aac", "-b:a", r["audio_bitrate"]]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart", str(staged_video)]
        run(cmd)

        video_check = probe(staged_video, cfg["ffprobe"])
        if (not video_check["has_video"] or video_check["width"] != int(r["width"])
                or video_check["height"] != int(r["height"])
                or not math.isfinite(video_check["duration"])
                or video_check["duration"] <= 0
                or abs(video_check["duration"] - info["duration"]) > max(1.0, info["duration"] * 0.02)):
            raise RuntimeError(f"标准化视频校验失败: {video_check}")
        # Verify the actual output cadence and square-pixel geometry, not just
        # the dimensions. ffprobe may return an unknown SAR for some codecs.
        cadence = subprocess.run(
            [cfg["ffprobe"], "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate,r_frame_rate,sample_aspect_ratio",
             "-of", "json", str(staged_video)], capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=60, check=True)
        video_streams = json.loads(cadence.stdout).get("streams", [])
        if not video_streams:
            raise RuntimeError("标准化视频缺少视频流")
        stream = video_streams[0]
        def frame_rate(value):
            try:
                numerator, denominator = value.split("/", 1)
                return float(numerator) / float(denominator)
            except (AttributeError, ValueError, ZeroDivisionError):
                return 0.0
        target_fps = float(r["fps"])
        actual_fps = frame_rate(stream.get("avg_frame_rate")) or frame_rate(stream.get("r_frame_rate"))
        sar = stream.get("sample_aspect_ratio")
        if (not math.isfinite(target_fps) or target_fps <= 0 or actual_fps <= 0
                or abs(actual_fps - target_fps) > 0.05 or sar not in ("1:1", "1/1")):
            raise RuntimeError(f"标准化帧率/SAR 校验失败: fps={actual_fps}, sar={sar}")

        audio_path = None
        if info["has_audio"]:
            run([cfg["ffmpeg"], "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                 "-c:a", "pcm_s16le", str(staged_audio)])
            audio_check = probe(staged_audio, cfg["ffprobe"])
            if (not audio_check["has_audio"] or audio_check["sample_rate"] != 16000
                    or audio_check["channels"] != 1 or not math.isfinite(audio_check["duration"])
                    or audio_check["duration"] <= 0):
                raise RuntimeError(f"标准化音频校验失败: {audio_check}")

        # Publish only complete, probed outputs; manifest is updated by prepare later.
        os.replace(staged_video, normalized)
        if info["has_audio"]:
            os.replace(staged_audio, audio)
            audio_path = str(audio)
    finally:
        staged_video.unlink(missing_ok=True)
        staged_audio.unlink(missing_ok=True)

    return {
        "name": name,
        "source": str(src),
        "video": str(normalized),
        "audio": audio_path,
        "has_audio": info["has_audio"],
        "duration": round(video_check["duration"], 3),
    }


def cached_outputs_valid(cached: dict, cfg: dict) -> bool:
    """Check that cached media still decodes and matches configured geometry."""
    try:
        video = probe(Path(cached['video']), cfg['ffprobe'])
        render = cfg['render']
        valid = (video['has_video'] and video['width'] == int(render['width'])
                 and video['height'] == int(render['height'])
                 and math.isfinite(video['duration']) and video['duration'] > 0
                 and abs(video['duration'] - float(cached['duration'])) <= 0.1)
        if cached.get('audio'):
            audio = probe(Path(cached['audio']), cfg['ffprobe'])
            valid = (valid and audio['has_audio'] and audio['sample_rate'] == 16000
                     and audio['channels'] == 1 and math.isfinite(audio['duration'])
                     and audio['duration'] > 0)
        return bool(valid)
    except (KeyError, OSError, ValueError, TypeError, subprocess.SubprocessError, json.JSONDecodeError):
        return False


def prepare(src: Path, cfg: dict, label: str | None = None) -> dict:
    src = src.resolve(strict=True)
    root = Path(cfg['root'])
    version = source_version(src, cfg['render'])
    label = project_name(label or src.stem)[:55]
    name = f"{label}-{version['asset_id']}-{version['version']}"
    manifest_path = root / 'work' / 'manifest.json'
    # Serialize each asset, then briefly lock the shared manifest during merge.
    with file_lock(root / 'work' / 'locks' / (version['asset_id'] + '.lock'), timeout=3600):
        with file_lock(manifest_path.with_suffix('.lock')):
            manifest = read_json(manifest_path) if manifest_path.exists() else {'items': []}
            cached = next((i for i in manifest['items'] if i.get('cache_name') == name
                           and i.get('source_version') == version), None)
        if cached and Path(cached['video']).is_file() and (not cached.get('audio') or Path(cached['audio']).is_file()):
            if cached_outputs_valid(cached, cfg):
                print(f'[缓存] {name}')
                return cached
        # Missing/partial outputs get a fresh generation, preserving referenced files.
        import uuid
        generation = name + '-' + uuid.uuid4().hex[:8]
        item = normalize(src, generation, cfg, root)
        if source_version(src, cfg['render']) != version:
            raise RuntimeError('预处理期间素材变化，未更新清单')
        item.update(source_version=version, label=label, cache_name=name)
        with file_lock(manifest_path.with_suffix('.lock')):
            manifest = read_json(manifest_path) if manifest_path.exists() else {'items': []}
            manifest['items'] = [i for i in manifest['items'] if str(Path(i['source']).resolve()).casefold() != str(src).casefold()] + [item]
            atomic_json(manifest_path, manifest)
        return item


def main() -> int:
    ap = argparse.ArgumentParser(description="AI cut 预处理")
    ap.add_argument("inputs", nargs="+", help="视频文件或目录")
    ap.add_argument("--out-name", help="单个输入时指定输出名")
    cli = ap.parse_args()

    cfg = load_config()
    root = Path(cfg["root"])
    files = collect_inputs(cli.inputs)
    if not files:
        print("没有找到可处理的视频", file=sys.stderr)
        return 1

    manifest_path = root / "work" / "manifest.json"
    processed = 0

    for src in files:
        name = cli.out_name if (cli.out_name and len(files) == 1) else src.stem
        print(f"[处理] {src.name} -> {name}.mp4")
        item = prepare(src, cfg, name)
        processed += 1
        note = "" if item["has_audio"] else "（无音轨，跳过语音识别）"
        print(f"        时长 {item['duration']}s {note}")

    print(f"\n完成 {processed} 个文件，清单: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
