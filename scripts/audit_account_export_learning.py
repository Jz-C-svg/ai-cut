"""Read-only integrity audit for the account-export learning corpus."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from aicut.learning_context import retrieve


def audit(dataset: Path) -> dict:
    dataset = Path(dataset).resolve()
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8-sig"))
    summary = json.loads((dataset / "learning-index-summary.json").read_text(encoding="utf-8-sig"))
    index_path = dataset / "learning-index.jsonl"
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    ids = collections.Counter(row.get("id") for row in rows)
    source_hashes = {row.get("sha256") for row in manifest}
    missing_sources = [row.get("source_video") for row in rows if not Path(row.get("source_video", "")).is_file()]
    unknown_hashes = [row.get("id") for row in rows if row.get("source_sha256") not in source_hashes]
    labels = [row for row in rows if row.get("kind") == "effect_timing" and row.get("evidence_status") == "human_reviewed_gold"]
    eligible = [row for row in labels if isinstance((row.get("decision_label") or {}).get("should_add_text"), bool)]

    audio_path = dataset / "production-audio/audio-event-v1/audio-event-candidates.jsonl"
    audio_rows = [json.loads(line) for line in audio_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    audio_missing, audio_hash_errors, audio_timing_errors = [], [], []
    for row in audio_rows:
        clip = Path(row["clip_file"])
        if not clip.is_file():
            audio_missing.append(row["example_id"])
            continue
        digest = hashlib.sha256(clip.read_bytes()).hexdigest()
        if digest != row.get("clip_sha256"):
            audio_hash_errors.append(row["example_id"])
        if float(row.get("end", 0)) <= float(row.get("start", 0)):
            audio_timing_errors.append(row["example_id"])

    image_summary = json.loads((dataset / "production-visual/image-text/summary.json").read_text(encoding="utf-8-sig"))
    image_rows = [json.loads(line) for line in (dataset / "production-visual/image-text/image-text-index.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    queries = {
        "transcript": "宝宝腹直肌口播台词", "kinetic_text": "花字标题位置和出现时间",
        "audio": "音效敲击瞬态声音", "loudness": "背景音乐音量响度",
        "editing": "镜头切点剪辑节奏", "cover": "账号素材图片封面",
    }
    expected_kinds = {"transcript": {"speech_transcript"}, "kinetic_text": {"kinetic_text"},
                      "audio": {"audio_event"}, "loudness": {"video_metadata"},
                      "editing": {"visual_shot"}, "cover": {"image_metadata", "image_text"}}
    retrieval = {}
    for key, query in queries.items():
        bundle = retrieve(query, root=ROOT, top_k=20)
        found = [item for item in bundle["evidence"] if item.get("category") == "account_export"]
        found_kinds = set(item.get("kind") for item in found)
        retrieval[key] = {"hits": len(found), "kinds": sorted(found_kinds),
                          "expected_kind_hit": bool(found_kinds & expected_kinds[key])}

    result = {
        "version": "account-export-learning-audit-v1", "dataset": str(dataset),
        "manifest_files": len(manifest), "index_rows": len(rows),
        "summary_count_matches": len(rows) == summary.get("records"),
        "duplicate_ids": sum(n - 1 for n in ids.values() if n > 1),
        "missing_source_references": len(missing_sources), "unknown_source_hashes": len(unknown_hashes),
        "effect_timing_gold_labels": len(labels), "eligible_reviewed_decisions": len(eligible),
        "audio_segments": len(audio_rows), "audio_missing_clips": len(audio_missing),
        "audio_clip_hash_errors": len(audio_hash_errors), "audio_timing_errors": len(audio_timing_errors),
        "image_expected": image_summary.get("images_expected"), "unique_images_ocr_processed": len(image_rows),
        "image_ocr_blocks": sum(len(row.get("text_blocks", [])) for row in image_rows),
        "image_ocr_errors": len(image_summary.get("errors", [])), "retrieval": retrieval,
        "unreferenced_audio_wav": len(list((dataset / "production-audio/audio-event-v1/clips").rglob("*.wav"))),
    }
    checks = [result["summary_count_matches"], result["duplicate_ids"] == 0,
              result["missing_source_references"] == 0, result["unknown_source_hashes"] == 0,
              result["audio_missing_clips"] == 0, result["audio_clip_hash_errors"] == 0,
              result["audio_timing_errors"] == 0, result["image_ocr_errors"] == 0,
              all(item["hits"] > 0 and item["expected_kind_hit"] for item in retrieval.values()),
              result["unreferenced_audio_wav"] == 0]
    result["all_passed"] = all(checks)
    result["failed_checks"] = [key for key, ok in zip(
        ("summary_count_matches", "duplicate_ids", "missing_source_references", "unknown_source_hashes",
         "audio_missing_clips", "audio_clip_hash_errors", "audio_timing_errors", "image_ocr_errors",
         "topic_retrieval", "unreferenced_audio_wav"), checks) if not ok]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    result = audit(Path(args.dataset))
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
