"""Smoke tests for fixed-font overlays and face-local beautification."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
spec = importlib.util.spec_from_file_location("export_video", ROOT / "scripts" / "export_video.py")
export_video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_video)
from aicut.portrait_beauty import soften_face
from aicut.text_overlays import split_caption_timeline, wrap_caption


class AdvancedRenderTests(unittest.TestCase):
    def test_caption_wrap_preserves_phrase_boundaries_and_respects_width(self):
        result = wrap_caption("第一步先准备奶粉，然后检查保质期。", 8)
        self.assertIn("，", result)
        self.assertLessEqual(max(map(len, result.splitlines())), 8)
        source = "咱们预产期从11月\n到12月的宝妈们"
        result = wrap_caption(source, 10)
        self.assertEqual("".join(result.splitlines()), "".join(source.splitlines()))
        self.assertEqual(len(result.splitlines()), 2)
        self.assertTrue(any("11月" in line for line in result.splitlines()))

    def test_single_line_caption_parts_are_sequential_and_preserve_text(self):
        source = [{"start": 1.0, "end": 4.0, "text": "咱们预产期从11月\n到12月的宝妈们"}]
        parts = split_caption_timeline(source, max_chars=12)
        self.assertEqual(len(parts), 2)
        self.assertEqual("".join(p["text"] for p in parts), "咱们预产期从11月到12月的宝妈们")
        self.assertTrue(all("\n" not in p["text"] for p in parts))
        self.assertLess(parts[0]["end"], parts[1]["start"])
        self.assertAlmostEqual(parts[0]["start"], 1.0)
        self.assertAlmostEqual(parts[-1]["end"], 4.0)
        phrase = [{"start": 0.0, "end": 6.0, "text": "咱们预产期从11月到12月的\n宝妈们"}]
        parts = split_caption_timeline(phrase, max_chars=12)
        self.assertEqual([p["text"] for p in parts], ["咱们预产期从11月", "到12月的宝妈们"])
        units = [{"start": 0.0, "end": 4.0, "text": "这个奶粉我们可以准备250毫升的"}]
        unit_parts = split_caption_timeline(units, max_chars=12)
        self.assertTrue(any("250毫升" in p["text"] for p in unit_parts))
        self.assertTrue(all(not p["text"].endswith("毫") for p in unit_parts))
        codes = [{"start": 0.0, "end": 4.0, "text": "这个奶嘴最好是SS码或者是S码"}]
        code_parts = split_caption_timeline(codes, max_chars=12)
        self.assertTrue(any("SS码" in p["text"] for p in code_parts))
        self.assertTrue(any("S码" in p["text"] for p in code_parts))
        baby = split_caption_timeline([{"start":0.0,"end":4.0,"text":"这样容易造成宝宝的乳头混淆"}], max_chars=12)
        self.assertTrue(all(not p["text"].endswith("宝") for p in baby))
        self.assertTrue(all(not p["text"].startswith("宝的") for p in baby))

    def test_single_line_ass_events_use_one_dialogue_per_caption(self):
        with tempfile.TemporaryDirectory(prefix="aicut-ass-single-line-") as tmp:
            root = Path(tmp)
            transcript = root / "subs.json"
            transcript.write_text(json.dumps({"segments": [
                {"start": 0.2, "end": 1.0, "text": "第一句"},
                {"start": 1.05, "end": 1.8, "text": "第二句"},
            ]}, ensure_ascii=False), encoding="utf-8")
            sheet = {"subtitles_json": str(transcript), "subtitle_layout": {
                "font_size_px": 58, "top_px": 1070, "single_line_per_caption": True,
                "background": False, "outline_width_px": 3, "shadow_px": 0,
                "margin_bottom_px": 732,
            }}
            ass = Path(export_video.ensure_ass(sheet, "single-line-ass-test"))
            source = ass.read_text(encoding="utf-8-sig")
            self.assertEqual(source.count("Dialogue: 0,"), 2)
            self.assertNotIn(r"\N", source)
            self.assertIn("Microsoft YaHei,58", source)
            self.assertIn("&HFF000000,1,0,0,0,100,100,0,0,1,3,0,2,60,60,732,1", source)
            ass.unlink(missing_ok=True)

    def test_drawtext_titles_and_subtitles_render_with_exact_font(self):
        font = Path(r"C:\Windows\Fonts\msyhbd.ttc")
        if not font.is_file():
            self.skipTest("Windows bold CJK font is unavailable")
        with tempfile.TemporaryDirectory(prefix="aicut-native-render-") as tmp:
            root = Path(tmp)
            source, out, subs = root / "source.mp4", root / "out.mp4", root / "subs.json"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=0x203040:s=1080x1920:r=30:d=2",
                "-f", "lavfi", "-i", "sine=frequency=700:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
            ], check=True, timeout=90)
            subs.write_text(json.dumps({"segments": [{
                "start": 0.2, "end": 1.8, "text": "字幕清楚可读\n短句排版测试",
            }]}, ensure_ascii=False), encoding="utf-8")
            sheet = {
                "width": 1080, "height": 1920, "fps": 30,
                "clips": [{"file": str(source), "start": 0, "end": 2, "speed": 1}],
                "subtitles_json": str(subs), "subtitle_layout": {
                    "font_size_px": 64, "top_px": 1080, "single_line_per_caption": True,
                    "max_chars_per_line": 12,
                },
                "kinetic_text_style": "festival",
                "kinetic_text": [{"start": 0.2, "end": 1.8, "kind": "step",
                                  "title": "第一步", "label": "奶粉", "subtitle": "独立花字合成",
                                  "animation": "pop", "effect": "glow"}],
                "render_pipeline": {"subtitle_renderer": "drawtext", "title_renderer": "drawtext",
                                    "font_path": str(font)},
            }
            command = export_video.build_command(sheet, out, with_subs=True)
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                self.assertAlmostEqual(export_video.probe_duration(str(out)), 2.0, delta=0.15)
                self.assertTrue(export_video.has_audio(str(out)))
                cap = cv2.VideoCapture(str(out)); cap.set(cv2.CAP_PROP_POS_MSEC, 900)
                ok, frame = cap.read(); cap.release()
                self.assertTrue(ok)
                self.assertGreater(float(np.std(frame[1000:1350])), 8.0,
                                   "caption background and glyphs should be rendered in the lower-safe region")
                self.assertGreater(float(np.std(frame[120:380])), 8.0,
                                   "kinetic title card should be rendered in the upper-safe region")
            finally:
                out.unlink(missing_ok=True)

    def test_face_softening_is_local_and_protects_eye_centers(self):
        rng = np.random.default_rng(4)
        frame = rng.integers(35, 220, (240, 320, 3), dtype=np.uint8)
        face = np.array([100, 65, 110, 130, 132, 108, 177, 108, 154, 137,
                         135, 165, 174, 165, 0.95], dtype=np.float32)
        result = soften_face(frame.copy(), face, 0.65)
        self.assertEqual(result.shape, frame.shape)
        self.assertGreater(int(cv2.absdiff(result[100:170, 118:192], frame[100:170, 118:192]).sum()), 0)
        self.assertEqual(int(cv2.absdiff(result[:45], frame[:45]).sum()), 0,
                         "pixels outside the face treatment should remain unchanged")
        self.assertLess(int(cv2.absdiff(result[101:115, 126:140], frame[101:115, 126:140]).sum()), 50000,
                        "eye-center mask should limit retouching around the eyes")


if __name__ == "__main__":
    unittest.main()
