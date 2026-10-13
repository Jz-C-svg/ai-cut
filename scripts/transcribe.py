"""语音转写：把音轨变成逐字稿 / SRT 字幕（AI cut / 阶段：解析层）。

用法:
    python transcribe.py <音轨或视频>... [--model small] [--name 名称]
    python transcribe.py <音轨>... --word-timestamps   # 额外输出词级时间戳
输出:
    02-transcript/<名称>.json   带时间码的分段（供剪辑决策使用）
    02-transcript/<名称>.srt    可直接导进剪映做字幕
    02-transcript/<名称>.txt    纯逐字稿（供文案拆解使用）
    02-transcript/<名称>.words.json  词级时间戳（--word-timestamps 或 WHISPER_WORD_TS=1 时）
"""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


def load_config() -> dict:
    return json.loads((Path(__file__).resolve().parent.parent / "config" / "aicut.json").read_text(encoding="utf-8"))


def fmt_time(seconds: float, srt: bool = False) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    sep = "," if srt else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def main() -> int:
    ap = argparse.ArgumentParser(description="AI cut 语音转写")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--model", help="tiny/base/small/medium/large-v3")
    ap.add_argument("--name", help="输出名（单文件时）")
    ap.add_argument("--device", help="cpu 或 cuda")
    ap.add_argument("--vocabulary", default="", help="本次素材的专业术语，逗号分隔；只提示识别，不自动改写原话")
    # v3.9.0：词级时间戳（开关或环境变量 WHISPER_WORD_TS=1）。开启后额外输出
    # <名称>.words.json，结构 {"name","language","duration","segments":[{start,end,text,
    # words:[{word,start,end,score}]}]}；原有 .json/.srt/.txt 输出不变。
    ap.add_argument("--word-timestamps", action="store_true",
                    help="输出词级时间戳 .words.json")
    ap.add_argument("--backend", choices=["faster-whisper", "whisperx"],
                    default="faster-whisper", help="ASR 后端（whisperx 当前不可用，见代码注释）")
    args = ap.parse_args()

    # v3.9.0：WhisperX 尝试结论（2026-09-12）：
    #   pip install whisperx 在本 venv（Python 3.14）失败——whisperx 3.2.0 锁定
    #   faster-whisper==1.0.0 / ctranslate2==4.4.0，该版本没有 py3.14 的 wheel，
    #   且会降级现有 faster-whisper 破坏主流程。故仅用 faster-whisper 内置 word_timestamps。
    #   TODO(speaker-diarization): WhisperX 说话人分离依赖 pyannote + HuggingFace 账号 token，
    #   按用户硬约束暂不接入；待 venv 迁到 py3.11/3.12 后再评估接 wav2vec2 强制对齐。
    if args.backend == "whisperx":
        print("[提示] WhisperX 后端在本环境不可用（py3.14 无 ctranslate2==4.4.0 wheel；"
              "说话人分离需 HF 账号 token，按约束不接）。自动回退 faster-whisper 内置词级时间戳。")
        args.backend = "faster-whisper"

    word_ts = args.word_timestamps or os.environ.get("WHISPER_WORD_TS", "0") == "1"

    cfg = load_config()
    root = Path(cfg["root"])
    asr = cfg["asr"]
    model_size = args.model or asr["model"]
    device = args.device or asr["device"]
    compute_type = asr["compute_type"]
    gpu_compute_type = asr.get("gpu_compute_type", "float16")
    requested_model, requested_device, requested_compute_type = model_size, device, compute_type
    # Use the configured quality setting; allow a per-process override for controlled tests.
    beam_size = int(os.environ.get("WHISPER_BEAM", asr.get("beam_size", 5)))

    from faster_whisper import WhisperModel

    # v3.2-B18：优先 CUDA/float16；检测不到（无 CUDA/cuDNN）自动降级 CPU/int8。
    # 显式传 --device 则完全按指定走。
    model = None
    if device in (None, "", "auto", "cuda"):
        try:
            print(f"[加载模型] {model_size} 尝试 cuda/{gpu_compute_type} ...")
            model = WhisperModel(model_size, device="cuda", compute_type=gpu_compute_type,
                                 download_root=asr["model_dir"])
            device, compute_type = "cuda", gpu_compute_type
        except Exception as exc:  # noqa: BLE001
            print(f"[回退] CUDA 不可用（{type(exc).__name__}: {str(exc)[:80]}）-> cpu/int8")
            device, compute_type = "cpu", "int8"
    if model is None:
        print(f"[加载模型] {model_size} ({device}/{compute_type}) beam={beam_size}")
        model = WhisperModel(model_size, device=device, compute_type=compute_type,
                             download_root=asr["model_dir"])

    out_dir = root / "02-transcript"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for raw in args.inputs:
        src = Path(raw)
        if not src.exists():
            print(f"[跳过] 不存在: {src}", file=sys.stderr)
            continue
        name = args.name if (args.name and len(args.inputs) == 1) else src.stem
        source_hash = hashlib.sha256()
        with src.open('rb') as source_stream:
            for block in iter(lambda: source_stream.read(1024 * 1024), b''):
                source_hash.update(block)
        try:
            fw_version = package_version('faster-whisper')
        except PackageNotFoundError:
            fw_version = 'unknown'
        try:
            ctranslate2_version = package_version('ctranslate2')
        except PackageNotFoundError:
            ctranslate2_version = 'unknown'
        print(f"[转写] {src.name}")
        transcribe_options = {
            "language": asr["language"], "beam_size": beam_size,
            "vad_filter": asr["vad"], "word_timestamps": word_ts,
            "initial_prompt": (f'专业术语：{args.vocabulary[:500]}。只转写实际说话，不添加词句。'
                               if args.vocabulary else None),
        }
        segments, info = model.transcribe(str(src), **transcribe_options)
        try:
            # faster-whisper yields lazily: CUDA DLL/runtime errors can happen
            # during iteration even when WhisperModel construction succeeded.
            segments = list(segments)
        except Exception as exc:
            if device != "cuda":
                raise
            print(f"[回退] CUDA 推理阶段失败（{type(exc).__name__}: {str(exc)[:120]}）-> cpu/int8")
            import gc
            del model
            gc.collect()
            device, compute_type = "cpu", "int8"
            model = WhisperModel(model_size, device=device, compute_type=compute_type,
                                 download_root=asr["model_dir"])
            segments, info = model.transcribe(str(src), **transcribe_options)
            segments = list(segments)
        rows = []
        rows_words = []
        for s in segments:
            text = s.text.strip()
            if not text:
                continue
            avg_logprob = float(getattr(s, "avg_logprob", 0.0) or 0.0)
            no_speech_prob = float(getattr(s, "no_speech_prob", 0.0) or 0.0)
            compression_ratio = float(getattr(s, "compression_ratio", 0.0) or 0.0)
            rows.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text,
                         "avg_logprob": round(avg_logprob, 4),
                         "no_speech_prob": round(no_speech_prob, 4),
                         "compression_ratio": round(compression_ratio, 4),
                         "低置信度": avg_logprob < -0.8 or no_speech_prob > 0.35 or compression_ratio > 2.4})
            if word_ts:
                words = [{"word": w.word, "start": round(float(w.start), 3),
                          "end": round(float(w.end), 3),
                          "score": round(float(getattr(w, "probability", 0.0) or 0.0), 3)}
                         for w in (s.words or [])]
                rows_words.append({"start": round(s.start, 2), "end": round(s.end, 2),
                                   "text": text, "words": words})

        (out_dir / f"{name}.json").write_text(
            json.dumps({"name": name, "language": info.language, "duration": round(info.duration, 2),
                        "segments": rows, "vocabulary_hint": args.vocabulary[:500],
                        "_asr": {"model": model_size, "requested_model": requested_model,
                                 "language": asr["language"],
                                 "device": device, "compute_type": compute_type,
                                 "requested_device": requested_device,
                                 "requested_compute_type": requested_compute_type,
                                 "gpu_compute_type": gpu_compute_type,
                                 "faster_whisper_version": fw_version,
                                 "ctranslate2_version": ctranslate2_version,
                                 "beam_size": beam_size, "vad": bool(asr["vad"]),
                                 "word_timestamps": word_ts, "vocabulary": args.vocabulary[:500],
                                 "source_sha256": source_hash.hexdigest()}},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / f"{name}.txt").write_text("\n".join(r["text"] for r in rows), encoding="utf-8")
        srt = []
        for i, r in enumerate(rows, 1):
            srt.append(f"{i}\n{fmt_time(r['start'], True)} --> {fmt_time(r['end'], True)}\n{r['text']}\n")
        (out_dir / f"{name}.srt").write_text("\n".join(srt), encoding="utf-8")
        if word_ts:
            (out_dir / f"{name}.words.json").write_text(
                json.dumps({"name": name, "language": info.language,
                            "duration": round(info.duration, 2), "segments": rows_words},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            n_words = sum(len(r["words"]) for r in rows_words)
            print(f"        词级时间戳 -> {name}.words.json（{n_words} 词）")

        print(f"        {len(rows)} 句 / {info.duration:.1f}s -> {name}.srt")
        results.append({"name": name, "segments": len(rows), "duration": round(info.duration, 2)})

    if not results:
        return 1
    print(f"\n完成 {len(results)} 个文件，输出目录: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
