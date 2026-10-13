"""Tests for balanced review batches and provenance-safe label merging."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from review_account_export import apply_labels, balanced_by_source, create_batch, write_jsonl


class AccountExportReviewTests(unittest.TestCase):
    def test_balanced_sampling_limits_same_source(self):
        rows = [{"example_id": str(i), "source_sha256": str(i % 3), "decision_label": {"status": "unreviewed"}}
                for i in range(30)]
        chosen = balanced_by_source(rows, 6, 10)
        self.assertEqual(len(chosen), 6)
        counts = {}
        for row in chosen:
            counts[row["source_sha256"]] = counts.get(row["source_sha256"], 0) + 1
        self.assertLessEqual(max(counts.values()), 2)

    def test_applies_reviewed_labels_with_backup_and_source_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            dataset = Path(temp)
            effect_path = dataset / "production-visual/kinetic-text/effect-decision-v1/decision-review-candidates.jsonl"
            audio_path = dataset / "production-audio/audio-event-v1/audio-event-candidates.jsonl"
            effect = {"example_id": "e1", "source_sha256": "a"*64, "time": 3.0,
                      "decision_label": {"status": "unreviewed", "should_add_text": None}}
            audio = {"example_id": "a1", "source_sha256": "b"*64, "start": 1.0, "end": 5.0,
                     "manual_label": {"status": "unreviewed", "contains_speech": None}}
            write_jsonl(effect_path, [effect]); write_jsonl(audio_path, [audio])
            submitted = dataset / "completed.jsonl"
            write_jsonl(submitted, [
                {"modality": "effect_timing", "example_id": "e1", "source_sha256": "a"*64, "time": 3.0,
                 "duration": 8.0, "decision_label": {"status": "reviewed", "should_add_text": True,
                 "should_add_effect": False, "best_timing_start": 2.5, "best_timing_end": 4.5,
                 "reason": "关键步骤提示", "reviewer": "tester"}},
                {"modality": "audio_event", "example_id": "a1", "source_sha256": "b"*64, "start": 1.0,
                 "end": 5.0, "manual_label": {"status": "reviewed", "contains_speech": True,
                 "contains_music": False, "contains_sfx": False, "class": "speech",
                 "reason": "可听见连续口播", "reviewer": "tester"}},
            ])
            result = apply_labels(dataset, submitted)
            self.assertEqual(result["applied_labels"], 2)
            self.assertTrue(effect_path.with_suffix(effect_path.suffix + ".pre-review.bak").exists())
            effect_after = json.loads(effect_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(effect_after["decision_label"]["status"], "reviewed")

    def test_rejects_incomplete_or_mismatched_label(self):
        with tempfile.TemporaryDirectory() as temp:
            dataset = Path(temp)
            effect_path = dataset / "production-visual/kinetic-text/effect-decision-v1/decision-review-candidates.jsonl"
            audio_path = dataset / "production-audio/audio-event-v1/audio-event-candidates.jsonl"
            write_jsonl(effect_path, [{"example_id": "e1", "source_sha256": "a"*64, "time": 1.0,
                                      "decision_label": {"status": "unreviewed"}}])
            write_jsonl(audio_path, [])
            completed = dataset / "bad.jsonl"
            write_jsonl(completed, [{"modality": "effect_timing", "example_id": "e1", "source_sha256": "x"*64,
                                     "time": 1.0, "decision_label": {"status": "reviewed"}}])
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                apply_labels(dataset, completed)

    def test_creates_playable_review_batch_without_pretending_labels(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); dataset = root / "dataset"; out = root / "review"
            effects = dataset / "production-visual/kinetic-text/effect-decision-v1/decision-review-candidates.jsonl"
            audios = dataset / "production-audio/audio-event-v1/audio-event-candidates.jsonl"
            sha = "a" * 64; source = root / "source.mp4"; source.write_bytes(b"video")
            frame = root / "frame.jpg"; frame.write_bytes(b"frame")
            write_jsonl(effects, [{"example_id":"e1", "source_sha256":sha, "source_video":str(source),
                "time":2.0, "evidence_frame":str(frame), "decision_label":{"status":"unreviewed"}}])
            clip = root / "clip.npz"; np.savez(clip, pcm=np.zeros(8000, dtype=np.float32), sample_rate=8000)
            write_jsonl(audios, [{"example_id":"a1", "source_sha256":sha, "source_video":str(source),
                "start":1.0, "end":2.0, "clip_file":str(clip), "manual_label":{"status":"unreviewed"}}])
            (dataset / "manifest.json").parent.mkdir(parents=True, exist_ok=True)
            (dataset / "manifest.json").write_text(json.dumps([{"kind":"video", "sha256":sha, "duration":10}],
                ensure_ascii=False), encoding="utf-8")
            result = create_batch(dataset, out, effect_limit=1, audio_limit=1)
            self.assertEqual(result["labels_filled_automatically"], 0)
            self.assertTrue((out / "review.html").exists())
            batch = [json.loads(line) for line in (out / "review-batch.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual({row["modality"] for row in batch}, {"effect_timing", "audio_event"})
            self.assertTrue(Path(batch[1]["playable_audio"]).exists())


if __name__ == "__main__":
    unittest.main()
