"""Tests for local-model title-card generation and evidence/time validation."""
import sys
import unittest
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from aicut.kinetic_text_agent import generate_cards, reviewed_decision_examples


CLIPS = [{"file": "clip.mp4", "media_id": "m1", "start": .9, "end": 3.1,
          "speed": 1, "first_segment": 1, "last_segment": 2}]
MEDIA = [{"id": "m1", "segments": [
    {"start": 1.0, "end": 2.0, "text": "这一步先稳定骨盆位置。"},
    {"start": 2.1, "end": 3.0, "text": "接着轻轻呼气。"},
]}]
VALID = {"decision": "add", "decision_reason": "突出动作步骤", "style": "festival", "cards": [{
    "media_id": "m1", "source_start": 1.0, "source_end": 2.0,
    "source_quote": "这一步先稳定骨盆位置", "title": "先稳定骨盆",
    "label": "这一步", "subtitle": "稳定骨盆位置", "reason": "强调原声步骤",
    "kind": "step", "animation": "pop", "effect": "glow",
}]}


class KineticTextAgentTests(unittest.TestCase):
    def invoke(self, response):
        calls = []
        def fake(messages, **kwargs):
            calls.append(kwargs)
            return response
        result = generate_cards(CLIPS, MEDIA, goal="讲清动作顺序", json_chat=fake)
        self.assertEqual(calls[0]["provider"], "ollama")
        self.assertTrue(calls[0]["json_mode"])
        return result

    def test_generates_local_source_anchored_cards(self):
        result = self.invoke(VALID)
        self.assertEqual(result["decision"], "add")
        self.assertEqual(result["source"], "local_ollama")
        self.assertEqual(result["style"], "festival")
        self.assertEqual(result["cards"][0]["start"], .1)

    def test_falls_back_when_model_fabricates_overlay_text(self):
        invalid = {**VALID, "cards": [{**VALID["cards"][0], "title": "保证有效"}]}
        result = self.invoke(invalid)
        self.assertEqual(result["decision"], "add")
        self.assertEqual(result["source"], "local_asr_rule_fallback")
        self.assertTrue(result["cards"])
        self.assertTrue(all(card["title"] in "".join(x["text"] for x in MEDIA[0]["segments"])
                            for card in result["cards"]))

    def test_falls_back_when_model_uses_unretained_source_timestamp(self):
        invalid = {**VALID, "cards": [{**VALID["cards"][0], "source_start": 5.0,
                                         "source_end": 6.0}]}
        result = self.invoke(invalid)
        self.assertIn(result["source"], {"local_asr_rule_fallback", "local_ollama"})
        self.assertTrue(all(card["title"] in "".join(x["text"] for x in MEDIA[0]["segments"])
                            for card in result["cards"]))

    def test_model_can_choose_no_overlay(self):
        result = self.invoke({"decision": "skip", "decision_reason": "口播和常规字幕已足够清楚",
                              "style": "clean", "cards": []})
        self.assertEqual(result["cards"], [])

    def test_only_human_reviewed_examples_are_retrieved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "labels.jsonl"
            rows = [
                {"transcript_context": "动作步骤稳定骨盆", "decision_label": {"status": "unreviewed", "should_add_text": True, "reason": "pending"}},
                {"transcript_context": "动作步骤稳定骨盆", "decision_label": {"status": "reviewed", "should_add_text": False, "should_add_effect": False, "reason": "已有字幕可读"}},
            ]
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            result = reviewed_decision_examples(path, "动作步骤稳定骨盆")
            self.assertEqual(len(result), 1)
            self.assertFalse(result[0]["should_add_text"])

    def test_default_dataset_prefers_latest_account_export(self):
        with tempfile.TemporaryDirectory() as temp:
            import aicut.paths as paths
            from unittest.mock import patch
            root = Path(temp)
            old = root / "08-data" / "local-datasets" / "账号导出-20260923" / "production-visual" / "kinetic-text" / "effect-decision-v1" / "decision-review-candidates.jsonl"
            new = root / "08-data" / "local-datasets" / "账号导出-20260927" / "production-visual" / "kinetic-text" / "effect-decision-v1" / "decision-review-candidates.jsonl"
            old.parent.mkdir(parents=True)
            new.parent.mkdir(parents=True)
            old.write_text("", encoding="utf-8")
            new.write_text("", encoding="utf-8")
            with patch.object(paths, "ROOT", root):
                result = generate_cards(CLIPS, MEDIA, goal="讲清动作顺序", json_chat=lambda *a, **k: {"decision":"skip","decision_reason":"不需要","style":"clean","cards":[]})
                expected = reviewed_decision_examples(new, "讲清动作顺序 这一步先稳定骨盆位置。 接着轻轻呼气。")
            self.assertEqual(result["reviewed_examples_used"], len(expected))


if __name__ == "__main__":
    unittest.main()
