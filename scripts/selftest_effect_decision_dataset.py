"""Synthetic tests for provenance-safe effect timing review dataset assembly."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_effect_decision_dataset import build


class EffectDecisionDatasetTests(unittest.TestCase):
    def test_builds_unreviewed_positive_and_control_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "source"
            visual = base / "production-visual"
            kinetic = visual / "kinetic-text"
            videos = visual / "videos" / "sha1"
            kinetic.mkdir(parents=True)
            videos.mkdir(parents=True)
            (base / "manifest.json").write_text(json.dumps([{"sha256": "sha1", "rights_status": "local_unverified"}]), encoding="utf-8")
            production = {"source": "local.mp4", "source_sha256": "sha1",
                          "frames": [{"time": 0, "file": "f0.jpg", "sha256": "f0", "review_status": "automatic_unreviewed"},
                                     {"time": 5, "file": "f5.jpg", "sha256": "f5"}],
                          "candidate_shots": [{"start": 0, "end": 6, "duration": 6,
                                               "transcript_segments": [{"start": 1, "end": 2, "text": "重点步骤"}]}]}
            ppath = videos / "production.json"
            ppath.write_text(json.dumps(production), encoding="utf-8")
            (visual / "index.json").write_text(json.dumps([{"sha256": "sha1", "production": str(ppath)}]), encoding="utf-8")
            event = {"source_sha256": "sha1", "id": "event1", "observations": [{
                "time": 0, "frame": "f0.jpg", "frame_sha256": "f0", "text": "重点步骤",
                "confidence": .9, "bbox_normalized": [0, .1, .5, .2], "vertical_zone": "upper"}]}
            (kinetic / "kinetic-text-candidates.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
            (kinetic / "reviewed-style-observations.jsonl").write_text("", encoding="utf-8")
            out = visual / "kinetic-text" / "effect-decision-v1"
            summary = build(base, out, per_class=1)
            rows = [json.loads(x) for x in (out / "decision-review-candidates.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(summary["human_gold_labels"], 0)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["decision_label"]["status"] == "unreviewed" for row in rows))
            self.assertTrue(all(row["decision_label"]["should_add_text"] is None for row in rows))
            self.assertEqual({row["candidate_basis"].startswith("large_text") for row in rows}, {True, False})
            rows[0]["decision_label"].update({"status": "reviewed", "should_add_text": True,
                                               "reason": "原声强调的操作步骤需要提示"})
            (out / "decision-review-candidates.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            build(base, out, per_class=1)
            rerun = [json.loads(x) for x in (out / "decision-review-candidates.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(row["decision_label"]["status"] == "reviewed" for row in rerun))

    def test_refuses_output_outside_dataset(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "new subdirectory"):
                build(source, Path(temp) / "outside")


if __name__ == "__main__":
    unittest.main()
