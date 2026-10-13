"""Safety and rendering contract tests for portable kinetic-text templates."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from aicut.kinetic_text import STYLE_PRESETS, ass_lines


CARD = {"start": 0.2, "end": 1.8, "kind": "title", "title": "标题",
        "label": "重点", "subtitle": "说明"}


class KineticTextRendererTests(unittest.TestCase):
    def test_supported_palettes_render_distinct_ass(self):
        outputs = {name: "\n".join(ass_lines([CARD], style=name)) for name in STYLE_PRESETS}
        self.assertEqual(len(outputs), 4)
        self.assertEqual(len(set(outputs.values())), 4)
        for output in outputs.values():
            self.assertIn("Dialogue:", output)
            self.assertIn("标题", output)

    def test_safe_animations_are_selectable(self):
        for animation in ("slide", "fade", "pop"):
            rendered = "\n".join(ass_lines([{**CARD, "animation": animation}]))
            self.assertIn("Dialogue:", rendered)
        self.assertIn("\\move(", "\n".join(ass_lines([CARD])))

    def test_safe_text_effects_are_selectable(self):
        for effect in ("none", "outline", "glow", "shadow"):
            rendered = "\n".join(ass_lines([{**CARD, "effect": effect}]))
            self.assertIn("Dialogue:", rendered)
        with self.assertRaisesRegex(ValueError, "unknown kinetic text effect"):
            ass_lines([{**CARD, "effect": "\\blur100"}])

    def test_unknown_template_or_animation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown kinetic text style"):
            ass_lines([CARD], style="{\\blur100}")
        with self.assertRaisesRegex(ValueError, "unknown kinetic text animation"):
            ass_lines([{**CARD, "animation": "\\blur100"}])


if __name__ == "__main__":
    unittest.main()
