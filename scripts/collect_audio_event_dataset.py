"""Collect review-first, timestamped audio events and signal features locally.

Reads source audio from the existing account-export manifest. It never writes
to source media and does not infer semantic sound-effect classes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np

from collect_local_dataset import save


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rms_db(x: np.ndarray) -> float:
    return float(20 * math.log10(max(float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))), 1e-8)))


def extract_pcm(source: Path, start: float, length: float, rate: int) -> np.ndarray:
    proc = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", f"{start:.6f}", "-i", str(source),
        "-t", f"{length:.6f}", "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-",
    ], capture_output=True, check=True, timeout=180)
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)


def extract_full_pcm(source: Path, rate: int) -> np.ndarray:
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0:a:0", "-vn",
                          "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                         capture_output=True, check=True, timeout=1200)
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)


def audio_features(x: np.ndarray, rate: int) -> dict:
    if not len(x):
        raise ValueError("empty audio segment")
    frame = max(1, int(rate * 0.05))
    usable = x[:len(x) // frame * frame]
    windows = usable.reshape(-1, frame) if len(usable) else x.reshape(1, -1)
    rms = np.sqrt(np.mean(np.square(windows, dtype=np.float64), axis=1))
    diff = np.diff(x.astype(np.float64), prepend=float(x[0]))
    n = min(len(x), rate)
    spectrum = np.abs(np.fft.rfft(x[:n] * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1 / rate)
    bands = {}
    for name, low, high in (("low_20_250", 20, 250), ("mid_250_2k", 250, 2000), ("high_2k_8k", 2000, 8000)):
        mask = (freqs >= low) & (freqs < high)
        bands[name] = float(np.sum(spectrum[mask] ** 2))
    total = sum(bands.values()) or 1e-12
    return {
        "rms_dbfs": rms_db(x),
        "peak_dbfs": float(20 * math.log10(max(float(np.max(np.abs(x))), 1e-8))),
        "crest_factor_db": float(20 * math.log10(max(float(np.max(np.abs(x))), 1e-8) / max(float(np.sqrt(np.mean(x.astype(np.float64) ** 2))), 1e-8))),
        "zero_crossing_rate": float(np.mean(np.signbit(x[1:]) != np.signbit(x[:-1]))) if len(x) > 1 else 0.0,
        "spectral_centroid_hz": float(np.sum(freqs * spectrum) / max(float(np.sum(spectrum)), 1e-12)),
        "band_energy_fraction": {key: value / total for key, value in bands.items()},
        "short_time_rms_dbfs_50ms": [float(20 * math.log10(max(float(v), 1e-8))) for v in rms],
        "transient_peak_candidates": int(np.sum(np.abs(diff) > max(float(np.std(diff)) * 7, 0.15))),
    }


def build(dataset: Path, out: Path, *, segment_seconds: float = 4.0, hop_seconds: float = 2.0,
          sample_rate: int = 16000) -> dict:
    manifest = read(dataset / "manifest.json")
    summary = read(dataset / "summary.json")
    rows = [r for r in manifest if r.get("kind") == "video" and r.get("status") == "ok"
            and r.get("audio") and not r.get("duplicate_of")]
    examples = []
    audio_root = out / "clips"
    audio_root.mkdir(parents=True, exist_ok=True)
    for row in rows:
        source = Path(row["file"])
        if not source.is_file():
            continue
        transcript_path = Path(row["transcript"]) if row.get("transcript") else None
        transcript = read(transcript_path).get("segments", []) if transcript_path and transcript_path.exists() else []
        duration = float(row.get("duration", 0))
        pcm_full = extract_full_pcm(source, sample_rate)
        audio_duration = len(pcm_full) / sample_rate
        duration = min(duration, audio_duration) if duration > 0 else audio_duration
        starts = list(np.arange(0, max(0.0, duration - segment_seconds) + 1e-6, hop_seconds))
        if duration > 0 and (not starts or starts[-1] + segment_seconds < duration):
            starts.append(max(0.0, duration - segment_seconds))
        for start in starts:
            end = min(duration, float(start) + segment_seconds)
            i0, i1 = int(float(start) * sample_rate), int(end * sample_rate)
            pcm = pcm_full[i0:i1]
            features = audio_features(pcm, sample_rate)
            overlaps = [s for s in transcript if float(s.get("start", 0)) < end and float(s.get("end", 0)) > start]
            key = f"{row['sha256']}|{float(start):.3f}|{end:.3f}"
            example_id = hashlib.sha256(key.encode()).hexdigest()[:24]
            clip_path = audio_root / row["sha256"] / f"{float(start):09.3f}.npz"
            clip_path.parent.mkdir(parents=True, exist_ok=True)
            if not clip_path.exists():
                np.savez_compressed(clip_path, pcm=pcm, sample_rate=np.int32(sample_rate))
            examples.append({
                "example_id": example_id, "source_sha256": row["sha256"], "source_video": str(source),
                "rights_status": row.get("rights_status", "unknown"), "start": round(float(start), 3),
                "end": round(end, 3), "duration": round(end-float(start), 3), "clip_file": str(clip_path),
                "clip_sha256": hashlib.sha256(clip_path.read_bytes()).hexdigest(),
                "transcript_context": " ".join(str(s.get("text", "")).strip() for s in overlaps).strip(),
                "transcript_review_status": "automatic_unreviewed",
                "features": features,
                "automatic_candidate_flags": [
                    name for name, condition in (
                        ("high_transient_density", features["transient_peak_candidates"] >= 8),
                        ("high_crest_factor", features["crest_factor_db"] >= 15),
                        ("high_frequency_energy", features["band_energy_fraction"]["high_2k_8k"] >= .35),
                    ) if condition
                ],
                "manual_label": {"status": "unreviewed", "class": None, "contains_sfx": None,
                                 "contains_music": None, "contains_speech": None,
                                 "speech_quality": None, "sfx_type": None,
                                 "recommended_edit": None, "reviewer": None, "reason": None},
            })

    out.mkdir(parents=True, exist_ok=True)
    target = out / "audio-event-candidates.jsonl"
    target.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in examples), encoding="utf-8")
    gold = sum(x["manual_label"]["status"] == "reviewed" for x in examples)
    result = {
        "version": "audio-event-review-v1", "source": summary.get("source"),
        "unique_video_sources_processed": len({x["source_sha256"] for x in examples}),
        "audio_segments": len(examples), "segment_seconds": segment_seconds, "hop_seconds": hop_seconds,
        "sample_rate_hz": sample_rate, "gold_labels": gold, "model_training_performed": False,
        "semantic_sound_classification_performed": False, "source_media_modified": False,
        "clip_audio_is_local_derivative": True, "rights_verified_for_redistribution": False,
    }
    save(out / "summary.json", result)
    (out / "README.md").write_text(
        "# 音频与音效候选数据集（待人工复核）\n\n"
        f"本地从账号导出中提取 {len(examples)} 条单声道 {sample_rate} Hz、最多 {segment_seconds:g} 秒音频片段（NPZ PCM），"
        "每段约每 2 秒滑动一次；均带原片哈希、源时间、片段哈希、转写上下文和短时声学特征。\n\n"
        "短时 RMS、峰值、crest factor、过零率、频带能量和瞬态计数是可复现的信号特征，不代表“鼓点/掌声/提示音”等语义类别。自动候选阈值只用于抽样复核。"
        "语音来自自动转写，未校对。请人工听片段后填写 manual_label；未复核片段不作为训练真值。\n\n"
        f"当前人工标签：{gold}；模型训练：未执行；原片修改：无。授权范围仅能确认本地处理，外部分发和训练许可未核验。\n",
        encoding="utf-8")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out")
    ap.add_argument("--segment-seconds", type=float, default=4.0)
    ap.add_argument("--hop-seconds", type=float, default=2.0)
    args = ap.parse_args()
    dataset = Path(args.dataset).resolve()
    out = Path(args.out).resolve() if args.out else dataset / "production-audio" / "audio-event-v1"
    if args.segment_seconds < 1 or args.hop_seconds <= 0:
        ap.error("segment seconds must be >=1 and hop seconds >0")
    print(json.dumps(build(dataset, out, segment_seconds=args.segment_seconds,
                           hop_seconds=args.hop_seconds), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
