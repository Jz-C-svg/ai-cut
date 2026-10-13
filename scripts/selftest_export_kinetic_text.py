"""End-to-end smoke test: render a kinetic text card without JianYing."""
import importlib.util
import re
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


class KineticTextExportTests(unittest.TestCase):
    def test_renders_dynamic_title_into_mp4_without_editor(self):
        with tempfile.TemporaryDirectory(prefix="aicut-kinetic-") as tmp:
            root = Path(tmp)
            output = root / f"{root.name}-rendered.mp4"
            source = root / "source.mp4"
            sfx = root / "sfx.wav"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=0x203040:s=1080x1920:r=30:d=2",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
            ], check=True, timeout=90)
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=0.2",
                "-c:a", "pcm_s16le", str(sfx),
            ], check=True, timeout=90)
            sheet = {
                "width": 1080, "height": 1920, "fps": 30,
                "clips": [{"file": str(source), "start": 0, "end": 2, "speed": 1}],
                "kinetic_text_style": "festival",
                "kinetic_text": [{"start": 0.2, "end": 1.8, "kind": "title",
                                  "title": "AI cut 花字验证", "label": "阶段一",
                                  "subtitle": "独立 MP4 渲染", "animation": "pop",
                                  "effect": "glow"}],
                "visual_effects": [{"type": "flash", "start": 0.55,
                                     "end": 0.70, "opacity": 0.28}],
                "sound_effects": [{"file": str(sfx), "at": 0.5, "volume": 0.3}],
            }
            command = export_video.build_command(sheet, output, with_subs=False)
            sidecar = ROOT / "work" / f"{output.stem}.kinetic.ass"
            self.assertIn(r"\3c&HFFC857&", sidecar.read_text(encoding="utf-8-sig"))
            try:
                proc = subprocess.run(command, capture_output=True, text=True, timeout=180)
                self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
                self.assertTrue(output.is_file())
                self.assertAlmostEqual(export_video.probe_duration(str(output)), 2.0, delta=0.15)

                cap = cv2.VideoCapture(str(output))
                cap.set(cv2.CAP_PROP_POS_MSEC, 900)
                ok, frame = cap.read()
                cap.release()
                self.assertTrue(ok)
                # The title card occupies the upper safe zone; rendered pixels must
                # differ materially from the flat-color source in that region.
                upper = frame[:420, :, :]
                self.assertGreater(float(np.std(upper)), 8.0)
                # A visible flash must alter the frame outside the title area.
                cap = cv2.VideoCapture(str(output))
                cap.set(cv2.CAP_PROP_POS_MSEC, 600)
                ok, flashed = cap.read()
                cap.release()
                self.assertTrue(ok)
                self.assertGreater(float(np.mean(flashed[500:1400, :, :])), 90.0)
                # The scheduled SFX must survive the final mix as audible audio.
                audio_check = subprocess.run([
                    "ffmpeg", "-hide_banner", "-i", str(output), "-af", "volumedetect",
                    "-f", "null", "-",
                ], capture_output=True, text=True, timeout=90)
                self.assertEqual(audio_check.returncode, 0, audio_check.stderr[-1000:])
                match = re.search(r"max_volume:\s*(-?[0-9.]+) dB", audio_check.stderr)
                self.assertIsNotNone(match, audio_check.stderr[-1000:])
                self.assertGreater(float(match.group(1)), -35.0,
                                   "scheduled sound effect should be present in final audio")
            finally:
                sidecar.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
