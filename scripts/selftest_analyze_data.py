"""Regression tests for source-bounded platform metric analysis."""
import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import analyze_data


class AnalyzeDataTests(unittest.TestCase):
    def test_report_binds_source_and_uses_observational_language(self):
        with tempfile.TemporaryDirectory(prefix="aicut-metrics-") as temp:
            root = Path(temp)
            source = root / "metrics.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["作品标题", "播放量", "完播率", "点赞", "收藏", "评论"])
                writer.writeheader()
                writer.writerow({"作品标题": "baseline", "播放量": 190, "完播率": 80,
                                 "点赞": 20, "收藏": 4, "评论": 10})
                writer.writerow({"作品标题": "low", "播放量": 10, "完播率": 20,
                                 "点赞": 10, "收藏": 0, "评论": 0})
            with patch.object(analyze_data, "ROOT", root), \
                 patch.object(sys, "argv", ["analyze_data.py", str(source), "--name", "test", "--window", "2"]):
                self.assertEqual(analyze_data.main(), 0)
            report = json.loads((root / "08-data" / "test.json").read_text(encoding="utf-8"))
            self.assertEqual(report["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(report["window_rows"], 2)
            rendered = (root / "08-data" / "test.md").read_text(encoding="utf-8")
            self.assertIn("原因未由这组数据确定", rendered)
            self.assertNotIn("钩子/封面/选题需要换", rendered)

    def test_rejects_empty_or_negative_baseline_window(self):
        for size in ("0", "-2"):
            with self.subTest(size=size), patch.object(sys, "argv", ["analyze_data.py", "unused.csv", "--window", size]):
                with self.assertRaises(SystemExit) as raised:
                    analyze_data.main()
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
