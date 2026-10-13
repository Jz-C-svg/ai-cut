"""Tests for source-preserving transition annotation normalization."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_autotransition_annotations import normalize


class AutoTransitionAnnotationTests(unittest.TestCase):
    def test_keeps_bad_and_zero_duration_events_out_of_visual_retrieval(self):
        with tempfile.TemporaryDirectory(prefix="autotransition-test-") as temp:
            root = Path(temp)
            source = root / "source.json"
            source.write_text(json.dumps({
                "templates": {
                    "train": {"v1": {"url": "https://source.invalid/v1", "transition": [
                        {"name": "fade", "start": 0, "end": 100, "duration": 100},
                        {"name": "direct_cut", "start": -5, "end": 0, "duration": 5},
                    ]}},
                    "test": {"v2": {"url": "https://source.invalid/v2", "transition": [
                        {"name": "direct_cut", "start": 100, "end": 100, "duration": 0},
                    ]}},
                }
            }), encoding="utf-8")
            manifest = normalize(source, root / "out")
            self.assertEqual(manifest["video_count"], 2)
            self.assertEqual(manifest["transition_event_count"], 3)
            self.assertEqual(manifest["invalid_time_event_count"], 1)
            self.assertEqual(manifest["visual_pairs_collected"], 0)
            events = [json.loads(line) for line in (root / "out/transition-events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[1]["time_validation"], "out_of_bounds_or_reversed")
            self.assertEqual(events[1]["event_kind"], "invalid_annotation")
            self.assertTrue(all(not row["retrieval_eligible"] for row in events))

    def test_rejects_video_id_repeated_across_splits(self):
        with tempfile.TemporaryDirectory(prefix="autotransition-test-") as temp:
            root = Path(temp)
            source = root / "source.json"
            item = {"transition": []}
            source.write_text(json.dumps({"templates": {"train": {"same": item}, "test": {"same": item}}}),
                              encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate video id"):
                normalize(source, root / "out")


if __name__ == "__main__":
    unittest.main()
