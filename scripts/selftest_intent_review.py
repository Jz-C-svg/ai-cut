import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aicut.intent_review import review_intent, review_visual_decisions


class ReviewTests(unittest.TestCase):
    def test_detects_ordinal_and_invented_audio(self):
        intent = {'逐句意图': [{'句号': 1, '理由': '第三步，保留'}],
                  'architecture_decision': {'CTA设计': '叠加文字‘收藏清单’，口播引导‘评论区交作业’'}}
        result = review_intent(intent, [{'text': '第二我们要准备帽子'}])
        self.assertTrue(result['blocking'])
        self.assertEqual({row['kind'] for row in result['issues']}, {'ordinal_mismatch', 'unavailable_spoken_cta'})
        self.assertEqual(len(result['issues']), 2)

    def test_text_overlay_is_not_treated_as_missing_speech(self):
        intent = {'逐句意图': [{'句号': 1, '理由': '第二步，保留'}],
                  'architecture_decision': {'CTA设计': '叠加文字‘收藏清单’'}}
        self.assertFalse(review_intent(intent, [{'text': '第二我们要准备帽子'}])['blocking'])

    def test_visual_card_without_current_frame_evidence_is_flagged(self):
        intent = {
            '逐句意图': [{'句号': 1, '知识依据': ['vdc-line-meaning-first-subtitle']}],
            'learning_context': {'evidence': [{'id': 'vdc-line-meaning-first-subtitle',
                'title': '字幕先按语义分句', 'category': 'visual_decision_card'}]},
            'learning_application': {'采用': []},
        }
        audit = review_visual_decisions(intent, [{'text': '一句口播'}], [])
        self.assertEqual(audit['status'], 'needs_review')
        self.assertEqual(audit['sentences'][0]['status'], 'needs_visual_review')
        self.assertFalse(audit['sentences'][0]['application_claimed'])

    def test_timestamped_profile_stays_candidate_only(self):
        intent = {
            '逐句意图': [{'句号': 1, '知识依据': ['vdc-line-meaning-first-subtitle']}],
            'learning_context': {'evidence': [{'id': 'vdc-line-meaning-first-subtitle',
                'title': '字幕先按语义分句'}]},
            'learning_application': {'采用': []},
        }
        profiles = [{'shot_id': 'shot-1', 't0': 0.0, 't1': 2.0,
                     'semantic_evidence': {'status': 'automatic_visual_observation_unreviewed'}}]
        audit = review_visual_decisions(intent, [{'text': '一句口播'}], profiles)
        self.assertEqual(audit['status'], 'candidate_for_confirmation')
        self.assertEqual(audit['sentences'][0]['status'], 'grounded_candidate')
        self.assertFalse(audit['sentences'][0]['application_claimed'])


if __name__ == '__main__':
    unittest.main()
