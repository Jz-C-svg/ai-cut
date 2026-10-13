"""Build a searchable, provenance-preserving index of account-export data."""
import argparse
import hashlib
import json
from pathlib import Path
from collect_local_dataset import save


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def build(dataset):
    dataset = Path(dataset)
    manifest = read_json(dataset / "manifest.json")
    docs = []

    def add(kind, sha, source, locator, text, status="automatic_unreviewed", **extra):
        text = " ".join(str(text).split())
        if not text:
            return
        key = "{}|{}|{}".format(sha, kind, locator)
        docs.append({"id": hashlib.sha256(key.encode()).hexdigest()[:24], "kind": kind,
                     "source_sha256": sha, "source_video": source, "locator": str(locator),
                     "text": text[:6000], "evidence_status": status,
                     "rights_status": "local_only_unverified", **extra})

    videos = [r for r in manifest if r.get("kind") == "video" and not r.get("duplicate_of")]
    seen_image_hashes = set()
    for row in manifest:
        if row.get("kind") == "image":
            if row.get("sha256") in seen_image_hashes:
                continue
            seen_image_hashes.add(row.get("sha256"))
            image = row.get("image", {})
            add("image_metadata", row.get("sha256", ""), row["file"], "image",
                "{} 图片宽度 {} 高度 {} 模式 {}".format(row.get("relative", ""), image.get("width"),
                                                          image.get("height"), image.get("mode")), "source_metadata")
        elif row.get("kind") == "archive":
            names = " ".join(item.get("name", "") for item in row.get("archive_members", [])[:100])
            add("archive_inventory", row.get("sha256", ""), row["file"], "archive",
                "压缩包清单 {} {}".format(row.get("relative", ""), names), "archive_member_inventory_unreviewed")
    image_text_path = dataset / "production-visual/image-text/image-text-index.jsonl"
    for image in read_jsonl(image_text_path):
        add("image_visual", image.get("source_sha256", ""), image.get("source", ""), "visual-features",
            "静态图片尺寸 {}x{} 平均亮度 {} 主色调 {}".format(
                image.get("width"), image.get("height"), image.get("mean_luma"), image.get("dominant_image_colors_bgr", [])),
            "automatic_visual_feature_unreviewed")
        for i, block in enumerate(image.get("text_blocks", [])):
            add("image_text", image.get("source_sha256", ""), image.get("source", ""),
                "{}#{}".format(i, block.get("bbox_normalized")),
                "图片文字 {} 位置 {} 置信度 {}".format(block.get("text", ""), block.get("vertical_zone"), block.get("confidence")),
                "automatic_ocr_unreviewed", confidence=block.get("confidence"),
                bbox_normalized=block.get("bbox_normalized"))
    audio_rows = read_jsonl(dataset / "production-audio/audio-event-v1/audio-event-candidates.jsonl")
    audio_by_sha = {}
    for row in audio_rows:
        audio_by_sha.setdefault(row.get("source_sha256"), []).append(row)

    for row in videos:
        sha, source = row["sha256"], row["file"]
        stream = next((s for s in row.get("metadata", {}).get("streams", []) if s.get("codec_type") == "video"), {})
        add("video_metadata", sha, source, "metadata", "{} 时长 {} 秒 视频编码 {} 音频声道 {} 响度 {} LUFS".format(
            row.get("relative", ""), row.get("duration"), stream.get("codec_name"),
            row.get("audio", {}).get("channels"), row.get("loudness", {}).get("integrated_lufs")), "source_metadata")
        audio_metrics = row.get("audio", {})
        add("audio_quality", sha, source, "audio-signal",
            "音频声道 RMS {} 平衡 {}dB 相关性 {} 单声道折叠损失 {}dB 侧中比 {}dB 真峰值 {}dBTP".format(
                audio_metrics.get("rms_dbfs"), audio_metrics.get("balance_db"), audio_metrics.get("correlation"),
                audio_metrics.get("mono_fold_loss_db"), audio_metrics.get("side_to_mid_db"),
                row.get("loudness", {}).get("true_peak_dbtp")), "measured_signal_feature")
        transcript_path = Path(row["transcript"]) if row.get("transcript") else None
        if transcript_path and transcript_path.exists():
            transcript = read_json(transcript_path)
            for i, seg in enumerate(transcript.get("segments", [])):
                add("speech_transcript", sha, source, "{}-{}#{}".format(seg.get("start"), seg.get("end"), i),
                    seg.get("text", ""), "automatic_asr_unreviewed", start=seg.get("start"), end=seg.get("end"),
                    asr_confidence=seg.get("avg_logprob"))
        prod_path = dataset / "production-visual/videos" / sha / "production.json"
        if prod_path.exists():
            prod = read_json(prod_path)
            for frame in prod.get("frames", []):
                add("visual_frame", sha, source, frame.get("time"),
                    "抽样画面 {} 秒 亮度 {} 清晰度拉普拉斯 {} 暗部比例 {} 高亮比例 {} 尺寸 {}x{}".format(
                        frame.get("time"), frame.get("mean_luma"), frame.get("laplacian_variance"),
                        frame.get("dark_pixel_ratio"), frame.get("bright_pixel_ratio"),
                        frame.get("width"), frame.get("height")), "automatic_visual_feature_unreviewed",
                    time=frame.get("time"), frame_sha256=frame.get("sha256"), image_file=frame.get("file"))
            for shot in prod.get("candidate_shots", []):
                spoken = " ".join(s.get("text", "") for s in shot.get("transcript_segments", []))
                add("visual_shot", sha, source, "{}-{}".format(shot.get("start"), shot.get("end")),
                    "候选镜头 {} 秒 {}".format(shot.get("duration"), spoken), "automatic_cut_candidate",
                    start=shot.get("start"), end=shot.get("end"))
        visual_path = dataset / "production-visual/videos" / sha / "visual-text.json"
        if visual_path.exists():
            for frame in read_json(visual_path).get("frames", []):
                for i, block in enumerate(frame.get("text_blocks", [])):
                    add("screen_text", sha, source, "{}#{}".format(frame.get("time"), i), block.get("text", ""),
                        "automatic_ocr_unreviewed", time=frame.get("time"), confidence=block.get("confidence"),
                        bbox_normalized=block.get("bbox_normalized"))
        for audio in audio_by_sha.get(sha, []):
            feat = audio.get("features", {})
            label = audio.get("manual_label") or {}
            status = "human_reviewed_gold" if label.get("status") == "reviewed" else "automatic_audio_feature_unreviewed"
            add("audio_event", sha, source, "{}-{}".format(audio.get("start"), audio.get("end")),
                ("音频片段 {}至{}秒 {} RMS {}dBFS 峰值 {}dBFS 频谱质心 {}Hz 瞬态候选 {}".format(
                    audio.get("start"), audio.get("end"), audio.get("transcript_context", ""),
                    feat.get("rms_dbfs"), feat.get("peak_dbfs"), feat.get("spectral_centroid_hz"),
                    feat.get("transient_peak_candidates")) + " crest {}dB 过零率 {} 频带能量 {} 自动候选 {}".format(
                    feat.get("crest_factor_db"), feat.get("zero_crossing_rate"), feat.get("band_energy_fraction"),
                    audio.get("automatic_candidate_flags", [])) + " 人工标签 {} {} 理由 {}".format(
                    label.get("class"), label, label.get("reason", ""))), status,
                start=audio.get("start"), end=audio.get("end"), clip_file=audio.get("clip_file"),
                flags=audio.get("automatic_candidate_flags", []), manual_label=label)

    kinetic_path = dataset / "production-visual/kinetic-text/kinetic-text-candidates.jsonl"
    for event in read_jsonl(kinetic_path):
        for obs in event.get("observations", []):
            add("kinetic_text", event.get("source_sha256", ""), event.get("source", ""),
                "{}#{}".format(obs.get("time"), event.get("id")),
                "花字候选文字 {} 位置 {} 置信度 {}".format(obs.get("text", ""), obs.get("vertical_zone"), obs.get("confidence")),
                "automatic_ocr_unreviewed", time=obs.get("time"), frame=obs.get("frame"), event_id=event.get("id"))
    styles_path = dataset / "production-visual/kinetic-text/reviewed-style-observations.jsonl"
    for style in read_jsonl(styles_path):
        add("reviewed_style_observation", style.get("source_sha256", ""), style.get("source", ""),
            style.get("id", ""), "已目视确认的静态样式观察 {} {}".format(
                style.get("style_label", ""), style.get("observed_style", {})),
            "human_reviewed_observation", time=style.get("observed_at"), frame=style.get("frame"),
            animation_reviewed=style.get("full_animation_reviewed", False))
    decision_path = dataset / "production-visual/kinetic-text/effect-decision-v1/decision-review-candidates.jsonl"
    for item in read_jsonl(decision_path):
        label = item.get("decision_label") or {}
        status = "human_reviewed_gold" if label.get("status") == "reviewed" else "automatic_candidate_unreviewed"
        add("effect_timing", item.get("source_sha256", ""), item.get("source_video", ""), item.get("example_id"),
            "花字特效时机候选 {} 画面文字 {} 候选依据 {} 标签 {} {} 理由 {}".format(
                item.get("transcript_context", ""), item.get("observed_screen_text", ""), item.get("candidate_basis", ""),
                label.get("should_add_text"), label.get("should_add_effect"), label.get("reason", "")),
            status, time=item.get("time"), example_id=item.get("example_id"), decision_label=label,
            frame_features=item.get("frame_features", {}))

    target = dataset / "learning-index.jsonl"
    target.write_text("".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs), encoding="utf-8")
    counts = {}
    for doc in docs:
        counts[doc["kind"]] = counts.get(doc["kind"], 0) + 1
    ids = [doc["id"] for doc in docs]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate learning-index id detected")
    summary = {"version": "account-export-learning-index-v1", "source": str(dataset), "records": len(docs),
               "by_kind": counts, "unique_video_sources": len(videos),
               "reviewed_gold": sum(d["evidence_status"] == "human_reviewed_gold" for d in docs),
               "automatic_unreviewed": sum("unreviewed" in d["evidence_status"] for d in docs),
               "source_media_modified": False, "model_training_performed": False,
               "rights_verified_for_redistribution": False, "index_file": str(target)}
    save(dataset / "learning-index-summary.json", summary)
    return summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    args = p.parse_args()
    print(json.dumps(build(args.dataset), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
