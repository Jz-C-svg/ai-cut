"""Real-run regressions: unsupported cover claims and perception context loss."""
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'scripts')]
import auto_edit


class LocalRunTests(unittest.TestCase):
    def test_invented_count_in_cover_is_replaced_by_source_excerpt(self):
        text = '有什么不同的地方可以在评论区留言'
        result, review = auto_edit.grounded_cover('抱姿3招，告别抱错！', [{'text': text}])
        self.assertIn(result, text)
        self.assertNotIn('3', result)
        self.assertEqual(review['status'], 'replaced_unsupported_copy')

    def test_nonnumeric_invented_claim_is_also_rejected(self):
        result, review = auto_edit.grounded_cover('科学抱娃告别错误', [{'text': '评论区留言'}])
        self.assertEqual(result, '评论区留言')
        self.assertEqual(review['status'], 'replaced_unsupported_copy')

    def test_exact_source_copy_is_kept(self):
        result, review = auto_edit.grounded_cover('不同的地方？', [{'text': '有什么不同的地方可以在评论区留言'}])
        self.assertEqual(result, '不同的地方？')
        self.assertEqual(review['status'], 'source_excerpt')

    def test_no_speech_does_not_claim_source_validation(self):
        _, review = auto_edit.grounded_cover('可见画面概括', [])
        self.assertEqual(review['status'], 'requires_visual_copy_review')

    def test_visual_query_keeps_full_recognition_separate(self):
        recognition = {'视觉状态': '成功', '视觉理解': [{'有效': True}], '文本理解': {'逐句': []}}
        original = copy.deepcopy(recognition)
        profiles = [{'semantic_evidence': {'status': 'automatic_visual_observation_unreviewed'},
                     '理解': {'主体': '人物', '动作': '抱着模型'}, 'shot_size': 'medium'},
                    {'理解': {'主体': '未核验内容'}}]
        terms = auto_edit.visual_query_terms(profiles)
        self.assertIn('抱着模型', terms)
        self.assertNotIn('未核验内容', terms)
        self.assertEqual(recognition, original)

    def test_adjacent_titles_render_only_the_next_title_on_boundary(self):
        from aicut.text_overlays import kinetic_filters
        with tempfile.TemporaryDirectory(prefix='aicut-title-boundary-') as directory:
            work = Path(directory)
            first = {'start': 0, 'end': 1, 'title': 'FIRST', 'animation': 'slide'}
            second = {'start': 1, 'end': 2, 'title': 'SECOND', 'animation': 'slide'}
            def frame(cards, stem):
                overlays, output = kinetic_filters({'kinetic_text': cards}, stem, work, '0:v', 2)
                graph = ';'.join(overlays + [f'[{output}]select=eq(n\\,15)[frame]'])
                result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=gray:s=540x960:r=15:d=2', '-filter_complex', graph,
                    '-map', '[frame]', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                    capture_output=True, check=True, timeout=30)
                return result.stdout
            actual = frame([first, second], 'adjacent')
            expected = frame([second], 'second-only')
            self.assertTrue(actual)
            self.assertEqual(actual, expected, 'Both titles were drawn on the shared boundary frame')


if __name__ == '__main__':
    unittest.main(verbosity=2)
