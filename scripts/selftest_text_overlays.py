"""Subtitle segmentation regressions for the normal export path."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from aicut.text_overlays import caption_filters, split_caption_timeline


class SubtitleOverlayTests(unittest.TestCase):
    def test_long_asr_line_becomes_sequential_single_line_captions(self):
        source = [{"start": 0, "end": 4.2,
                   "text": "前面给大家讲了是宝宝饮食方面的需要准备的一些东西"}]
        parts = split_caption_timeline(source, max_chars=10)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all("\n" not in p["text"] for p in parts))
        self.assertEqual("".join(p["text"] for p in parts), source[0]["text"])
        self.assertEqual(parts[0]["start"], source[0]["start"])
        self.assertEqual(parts[-1]["end"], source[0]["end"])
        for left, right in zip(parts, parts[1:]):
            self.assertLess(left["end"], right["start"])
            self.assertAlmostEqual(right["start"] - left["end"], .035, places=3)

    def test_normal_export_layout_uses_the_phrase_splitter(self):
        with tempfile.TemporaryDirectory(prefix="aicut-subtitle-") as temp:
            root = Path(temp)
            transcript = root / "transcript.json"
            transcript.write_text(json.dumps({"segments": [{
                "start": 0, "end": 4.2,
                "text": "前面给大家讲了是宝宝饮食方面的需要准备的一些东西",
            }]}), encoding="utf-8")
            sheet = {
                "subtitles_json": str(transcript),
                "subtitle_layout": {"single_line_per_caption": True,
                                    "font_size_px": 58, "top_px": 1055,
                                    "max_chars_per_line": 10},
                "render_pipeline": {"font_path": r"C:\Windows\Fonts\msyhbd.ttc"},
            }
            filters, output = caption_filters(sheet, "sample", root / "work", "v0", 5)
            self.assertEqual(output, f"cap{len(filters) - 1}")
            self.assertGreater(len(filters), 1)
            text_files = sorted((root / "work").glob("sample.caption.*.txt"))
            self.assertEqual(len(text_files), len(filters))
            self.assertTrue(all("\n" not in p.read_text(encoding="utf-8") for p in text_files))
            intervals = []
            for graph in filters:
                match = re.search(r"enable='between\(t,([0-9.]+),([0-9.]+)\)'", graph)
                self.assertIsNotNone(match)
                intervals.append(tuple(map(float, match.groups())))
            for left, right in zip(intervals, intervals[1:]):
                self.assertLess(left[1], right[0])


if __name__ == "__main__":
    unittest.main()
