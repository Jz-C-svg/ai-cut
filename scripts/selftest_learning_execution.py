"""Isolated regressions for source-bound semantic subtitle execution."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'scripts')]
from aicut.learning_execution import compile_subtitles, phrase_parts, verify_draft_subtitles
from aicut.text_overlays import split_caption_timeline, caption_filters
import export_video


class SubtitleLearningTests(unittest.TestCase):
    def setUp(self):
        self.text = '前面给大家讲了是宝宝饮食方面的需要准备的一些东西'
        self.parts = ['前面给大家讲了', '是宝宝饮食方面的', '需要准备的一些东西']
        self.rows = [{'start': .2, 'end': 6.2, 'text': self.text}]
        self.card = {'id': 'subtitle-rule', 'category': 'visual_decision_card',
            'topic_group': '字幕', 'title': '字幕按语义分段', 'text': '字幕先按语义分句',
            'evidence_type': 'human_derived_transferable_rule',
            'evidence_refs': [{'source_url': 'https://example.test/reference.webm'}],
            'source': 'fixture', 'decision_rule': '先依自然停顿和语义闭合切成短句'}
        self.action = {'证据id': self.card['id'], '对应句号': [1],
            '具体改变': '按照原话的语义拆成三段字幕', '素材依据': '当前句有前言、对象和准备内容',
            '执行参数': {'type': 'subtitle_phrases', 'phrases': self.parts}}
        self.intent = {'learning_context': {'query': '字幕分段', 'evidence': [self.card]},
            '逐句意图': [{'句号': 1, '句': self.text, '知识依据': [self.card['id']]}],
            'learning_application': {'采用': [self.action]}}

    def test_compilation_preserves_source_and_generates_distinct_events(self):
        original = copy.deepcopy(self.rows)
        compiled, audit = compile_subtitles(self.intent, self.rows)
        self.assertEqual(audit['compiled_count'], 1)
        self.assertFalse(audit['effect_verified'])
        self.assertEqual(self.rows, original)
        events = split_caption_timeline(compiled)
        self.assertEqual([e['text'] for e in events], self.parts)
        self.assertNotEqual([e['text'] for e in split_caption_timeline(self.rows)], self.parts)
        self.assertTrue(all(a['end'] < b['start'] for a, b in zip(events, events[1:])))
        self.assertEqual(events, split_caption_timeline(events))

    def test_added_deleted_reordered_or_multiline_text_rejected(self):
        for parts in ([self.parts[0], '宝宝需要奶瓶'], list(reversed(self.parts)),
                      [self.parts[0], '\n'.join(self.parts[1:])], [self.text]):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                phrase_parts(self.text, parts)

    def test_catalog_or_uncited_evidence_cannot_execute(self):
        for change in ('public_dataset', 'uncited', 'missing_source'):
            intent = copy.deepcopy(self.intent)
            if change == 'public_dataset':
                intent['learning_context']['evidence'][0]['category'] = change
            elif change == 'uncited':
                intent['逐句意图'][0]['知识依据'] = []
            else:
                intent['learning_context']['evidence'][0]['evidence_refs'] = []
            rows, audit = compile_subtitles(intent, self.rows)
            self.assertEqual(audit['compiled_count'], 0)
            self.assertNotIn('caption_phrases', rows[0])

    def test_reordered_output_matches_text_and_ambiguous_output_is_rejected(self):
        unrelated = {'start': 0, 'end': .2, 'text': '开场'}
        _, audit = compile_subtitles(self.intent, [unrelated, *self.rows])
        self.assertEqual(audit['entries'][0]['output_index'], 1)
        _, audit = compile_subtitles(self.intent, self.rows * 2)
        self.assertEqual(audit['compiled_count'], 0)
        self.assertIn('ambiguous', audit['entries'][0]['reason'])

    def test_short_individual_phrase_does_not_pass_average_duration_check(self):
        with self.assertRaisesRegex(ValueError, 'minimum'):
            split_caption_timeline([{'start': 0, 'end': 1,
                'text': '这是一个长长的句子啊', 'caption_phrases': ['这是一个长长的句子', '啊']}])

    def test_numbers_units_and_english_words_are_not_split(self):
        for text, parts in [('准备120毫升温水', ['准备120毫', '升温水']),
                            ('加入OpenAI接口', ['加入Open', 'AI接口'])]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'boundary'):
                phrase_parts(text, parts)

    def test_ass_and_drawtext_consume_same_semantic_parts(self):
        compiled, _ = compile_subtitles(self.intent, self.rows)
        with tempfile.TemporaryDirectory(prefix='aicut-caption-consumers-') as raw:
            root = Path(raw)
            work = root / 'work'
            work.mkdir()
            subs = root / 'subs.json'
            subs.write_text(json.dumps({'segments': compiled}, ensure_ascii=False), encoding='utf-8')
            sheet = {'subtitles_json': str(subs), 'subtitle_layout': {
                'single_line_per_caption': True, 'max_chars_per_line': 12}}
            with patch.object(export_video, 'ROOT', root):
                ass = Path(export_video.ensure_ass(sheet, 'learned')).read_text(encoding='utf-8-sig')
            filters, _ = caption_filters(sheet, 'learned', work, 'v', 6.2)
            self.assertEqual(len(filters), 3)
            self.assertEqual(ass.count('Dialogue:'), 3)
            for i, part in enumerate(self.parts):
                self.assertIn(part, ass)
                self.assertEqual((work / f'learned.caption.{i:03d}.txt').read_text(encoding='utf-8'), part)

    def test_native_audit_rejects_wrong_text_or_timing_despite_same_count(self):
        _, audit = compile_subtitles(self.intent, self.rows)
        events = audit['entries'][0]['expected_events']
        draft = {'materials': {'texts': [{'id': str(i), 'content': json.dumps({'text': e['text']})}
                  for i, e in enumerate(events)]}, 'tracks': [{'name': 'subtitle',
            'segments': [{'material_id': str(i), 'target_timerange': {
                'start': round(e['start'] * 1e6), 'duration': round((e['end'] - e['start']) * 1e6)}}
                for i, e in enumerate(events)]}]}
        self.assertEqual(verify_draft_subtitles(audit, draft)['status'], 'complete')
        draft['materials']['texts'][0]['content'] = json.dumps({'text': '错误台词'})
        self.assertEqual(verify_draft_subtitles(audit, draft)['status'], 'needs_review')
        draft['materials']['texts'][0]['content'] = json.dumps({'text': events[0]['text']})
        draft['tracks'][0]['segments'][0]['target_timerange']['start'] += 100000
        self.assertEqual(verify_draft_subtitles(audit, draft)['status'], 'needs_review')

    def test_model_claim_without_execution_parameters_triggers_repair(self):
        from aicut.edit_intent import _validate_subtitle_proposals
        _validate_subtitle_proposals(self.intent, self.rows, self.intent['learning_context'])
        intent = copy.deepcopy(self.intent)
        del intent['learning_application']['采用'][0]['执行参数']
        with self.assertRaisesRegex(ValueError, '执行参数'):
            _validate_subtitle_proposals(intent, self.rows, intent['learning_context'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
