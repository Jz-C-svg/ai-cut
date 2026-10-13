"""Regression tests for real bridge writes, not Jianying host acceptance."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from editor_bridges.base import EditorProject, EditorTrack, EditorSegment
from editor_bridges.jianying import JianYingBridge
from editor_bridges.jianying.integrity import check_content


class DraftIntegrityTests(unittest.TestCase):
    def project(self):
        return EditorProject("test", tracks=[
            EditorTrack("v", "video", [EditorSegment("v1", "video", 0, 2, material="same.mp4", src_in=1, src_len=2),
                                       EditorSegment("v2", "video", 2, 2, material="same.mp4", src_in=8, src_len=2)]),
            EditorTrack("a", "audio", [EditorSegment("a1", "audio", 0, 4, material="same.mp4")]),
            EditorTrack("t", "text", [EditorSegment("t1", "text", 0, 2, text="same.mp4")]),
        ], duration=1)

    def test_types_duration_and_readback(self):
        with tempfile.TemporaryDirectory() as folder:
            b = JianYingBridge(); p = b.write(self.project(), folder)
            d = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(set(d["materials"]), {"videos", "audios", "texts"})
            self.assertEqual(d["duration"], 4_000_000)
            self.assertEqual(d["materials"]["videos"][0]["duration"], 10_000_000)
            self.assertEqual(len({m["id"] for g in d["materials"].values() for m in g}), 3)
            self.assertTrue(b.validate(p).valid)
            read = b.read(p)
            self.assertEqual(read.tracks[0].segments[1].src_in, 8)
            self.assertEqual(read.tracks[0].segments[0].material, "same.mp4")

    def test_asset_copy_collision_and_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "same.mp4").write_bytes(b"synthetic-media-fixture")
            project = self.project(); project.extra = {"self_contained_assets": True, "media_root": str(root)}
            p = JianYingBridge().write(project, root / "draft")
            manifest = json.loads((p.parent / "assets.manifest.json").read_text())
            self.assertEqual(len(manifest["assets"]), 1)
            read = JianYingBridge().read(p)
            self.assertTrue(Path(read.tracks[0].segments[0].material).is_file())
            project.tracks[0].segments[0].material = "missing.mp4"
            with self.assertRaises(FileNotFoundError):
                JianYingBridge().write(project, root / "missing")
            self.assertFalse((root / "missing/draft_content.json").exists())

    def test_reject_invalid_timing_before_write(self):
        for field, value in [("length", -1), ("t", float("nan")), ("speed", 0)]:
            p = self.project(); setattr(p.tracks[0].segments[0], field, value)
            with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
                JianYingBridge().write(p, folder)
                self.fail("invalid draft accepted")

    def test_overlap_and_duplicates(self):
        p = self.project(); p.tracks[0].segments[1].t = 1
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            JianYingBridge().write(p, folder)
        p = self.project(); p.tracks[0].segments[1].id = "v1"
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            JianYingBridge().write(p, folder)

    def test_dangling_and_malformed_materials(self):
        self.assertTrue(check_content({"tracks": [{"segments": [{"material_id": "bad"}]}]}))
        self.assertTrue(check_content({"tracks": [], "materials": {"videos": "bad"}}))
        self.assertTrue(check_content([]))


if __name__ == "__main__":
    unittest.main()
