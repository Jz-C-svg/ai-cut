"""Isolated learning retrieval, decision grounding and pipeline integration tests."""
import contextlib
import copy
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT / 'scripts'))
from aicut import learning_context as learning


class LearningTests(unittest.TestCase):
    def test_creative_writing_playbook_is_reserved_for_story_tasks(self):
        playbook = ROOT / 'content-library/creative-writing-playbook.json'
        if not playbook.is_file():
            self.skipTest('project playbook not installed')
        result = learning.retrieve('月子中心 剧情短视频 文案 主角 目标 阻碍 轻喜剧 误会 故事服务', root=ROOT)
        evidence = learning.selected_evidence(result)
        matches = [d for d in evidence if d.get('source') == 'content-library/creative-writing-playbook.json']
        self.assertTrue(matches)
        self.assertEqual(len(matches), 3)
        self.assertTrue(any('/rules/0' in d['locator'] for d in matches))

    def test_paired_registry_deduplicates_and_excludes_unvalidated_sidecars(self):
        directory = self.root / 'content-library/web-visuals/paired-samples'
        directory.mkdir(parents=True)
        video=directory/'fixture.mp4'; video.write_bytes(b'fixture-video-not-real-media')
        frame=directory/'fixture.jpg'; frame.write_bytes(b'fixture-frame')
        vh=hashlib.sha256(video.read_bytes()).hexdigest()
        fh=hashlib.sha256(frame.read_bytes()).hexdigest()
        row={'id':'paired-a','title':'字幕教学','category':'花字','source_url':'https://example.test/a',
             'status':'framewise_vision_complete','video_path':video.relative_to(self.root).as_posix(),
             'video_sha256':vh,'video_metadata':{'decoded_frames':1},
             'frames':[{'path':frame.relative_to(self.root).as_posix(),'sha256':fh,'time_seconds':0}],
             'frame_observations':[{'frame_path':frame.relative_to(self.root).as_posix(),'frame_sha256':fh,'time_seconds':0,'status':'observed','vision_analysis':{'fact':'fixture'}}],
             'frame_observation_count':1,'published_at':'2026-10-04',
             'analysis_method':'fixture_only','limitations':['sampled frames only'],
             'vision_model':'fixture','review_status':'automatic_analysis_not_human_reviewed',
             'ai_rag_eligible':True,'usage_scope':'commercially_permitted'}
        research_only={**row,'id':'research-only','ai_rag_eligible':False,'usage_scope':'research_only'}
        duplicate={**row,'id':'paired-b'}
        (directory/'studies.jsonl').write_text('\n'.join(json.dumps(r) for r in [row,row,research_only,duplicate]),encoding='utf-8')
        (directory/'pending.json').write_text('{"unverified":"should not become knowledge"}',encoding='utf-8')
        data=learning.catalog(self.root)
        self.assertEqual([d['id'] for d in data['docs'] if d['category']=='paired_visual_study'],['paired-a'])
        self.assertEqual(len([w for w in data['warnings'] if 'invalid paired visual study' in w]),3)
        self.assertFalse(any('pending.json' in d.get('source','') for d in data['docs']))
        paired=next(d for d in data['docs'] if d['category']=='paired_visual_study')
        selected=learning.selected_evidence({'query':'字幕剪辑','evidence':[paired]})[0]
        self.assertEqual(selected['published_at'],'2026-10-04')
        self.assertEqual(selected['limitations'],['sampled frames only'])
        self.assertEqual(selected['analysis_method'],'fixture_only')

    def test_paired_research_only_corpus_is_not_loaded_into_ai_rag(self):
        directory = self.root / 'content-library/web-visuals/paired-samples'
        directory.mkdir(parents=True)
        video=directory/'research.mp4'; video.write_bytes(b'research-video')
        frame=directory/'research.jpg'; frame.write_bytes(b'research-frame')
        vh=hashlib.sha256(video.read_bytes()).hexdigest()
        fh=hashlib.sha256(frame.read_bytes()).hexdigest()
        row={'id':'fivek-research','title':'research sample','category':'color grading',
             'source_url':'https://example.test/fivek','status':'framewise_vision_complete',
             'video_path':video.relative_to(self.root).as_posix(),'video_sha256':vh,
             'video_metadata':{'decoded_frames':1},
             'frames':[{'path':frame.relative_to(self.root).as_posix(),'sha256':fh,'time_seconds':0}],
             'frame_observations':[{'frame_sha256':fh,'time_seconds':0,'status':'observed',
                                    'vision_analysis':{'fact':'research'}}],
             'vision_model':'fixture','review_status':'automatic_analysis_not_human_reviewed',
             'ai_rag_eligible':False,'usage_scope':'research_only'}
        (directory/'studies.jsonl').write_text(json.dumps(row),encoding='utf-8')
        data=learning.catalog(self.root)
        self.assertFalse(any(d.get('id')=='fivek-research' for d in data['docs']))

    def test_existing_external_visual_studies_are_quarantined_from_rag(self):
        data = learning.catalog(ROOT)
        paired_ids = [d.get('id') for d in data['docs'] if d.get('category') == 'paired_visual_study']
        self.assertEqual(paired_ids, [])
        self.assertFalse((ROOT / 'content-library/web-visuals/paired-samples/studies.jsonl').exists())
        self.assertTrue((ROOT / 'work/visual-study-quarantine-20261006/manifest.json').is_file())

    def test_external_dataset_staging_area_is_not_scanned_as_brain_knowledge(self):
        paths = learning._files(ROOT)
        self.assertFalse(any('external-datasets' in p.parts for p in paths))

    def test_visual_decision_cards_retrieve_with_use_avoid_and_validation(self):
        path = self.root / 'content-library/rag/visual_decision_cards.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {'id':'vdc-subtitle-fixture','title':'逐句字幕与关键词强调','topic_group':'字幕',
               'topics':['字幕','花字','分段'], 'retrieval_labels':['逐句显示','台词分段'],
               'when_to_use':'口播需要逐句同步字幕时',
               'decision_rule':'按语义闭合分句，每段只强调一个关键词。',
               'avoid_when':'不要按固定字数硬切或遮挡手部。',
               'validation':'核对语音起止、单屏可读和主体遮挡。',
               'evidence_type':'human_derived_transferable_rule',
               'evidence_refs':[{'source_url':'https://example.test/source',
                                 'observed_fact':'样例把词语分拍呈现。'}]}
        path.write_text(json.dumps(row, ensure_ascii=False)+'\n', encoding='utf-8')
        bundle = learning.retrieve('口播字幕按台词分段，关键词花字', root=self.root)
        card = next(d for d in bundle['evidence'] if d['category']=='visual_decision_card')
        rendered = learning.selected_evidence(bundle)
        card = next(d for d in rendered if d['category']=='visual_decision_card')
        self.assertIn('不要按固定字数硬切', card['avoid_when'])
        self.assertIn('语音起止', card['validation'])
        self.assertIn('when_to_use', card)

    def test_visual_decision_cards_reject_untraceable_rows(self):
        path = self.root / 'content-library/rag/visual_decision_cards.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        valid = {'id':'vdc-no-source','title':'无证据卡','topics':['特效'],
                 'when_to_use':'总是','decision_rule':'总是使用', 'avoid_when':'无',
                 'validation':'无', 'evidence_refs':[],
                 'evidence_type':'human_derived_transferable_rule'}
        path.write_text(json.dumps(valid, ensure_ascii=False)+'\n', encoding='utf-8')
        data = learning.catalog(self.root)
        self.assertFalse(any(d.get('id')=='vdc-no-source' for d in data['docs']))
        self.assertTrue(any('invalid visual decision card' in w for w in data['warnings']))

    def test_action_match_card_retrieves_and_keeps_source_scope(self):
        bundle = learning.retrieve('动作中切镜，手部动作方向连续，匹配切点', root=ROOT)
        cards = [row for row in learning.selected_evidence(bundle)
                 if row.get('id') == 'vdc-cut-on-matching-action']
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertIn('不要硬套动作匹配', card['avoid_when'])
        self.assertIn('目标应一致', card['validation'])
        self.assertIn('Cutting_on_action.webm', card['evidence_refs'][0]['source_url'])
        prompt = learning.prompt_context(bundle)
        self.assertIn('动作仍在进行时切到能接住同一动作的镜头', prompt)
        self.assertIn('不要硬套动作匹配', prompt)

    def test_action_match_study_media_frames_and_hashes_are_paired(self):
        study_path = ROOT / 'content-library/web-visuals/external-datasets/cutting-on-action/visual-study.json'
        study = json.loads(study_path.read_text(encoding='utf-8'))
        self.assertEqual(study['video_metadata']['decoded_frames'], 235)
        self.assertTrue(study['ai_rag_eligible'])
        self.assertFalse(study['production_use_allowed'])
        video = study_path.parent / study['video_path']
        self.assertEqual(hashlib.sha256(video.read_bytes()).hexdigest(), study['video_sha256'])
        for frame in study['frames']:
            path = study_path.parent / frame['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), frame['sha256'])
        self.assertEqual([frame['time_seconds'] for frame in study['frames']], [3.5, 4.0, 4.5, 5.0])

    def test_dataset_catalog_cannot_authorize_deletion(self):
        segments=[{'start':i,'end':i+1,'text':'重复讲述'} for i in range(8)]
        intent={'learning_context':{'evidence':[{'id':'catalog','category':'public_dataset'}]},
                '逐句意图':[{'句号':2,'动作':'删除','置信度':.99,'理由':'重复','知识依据':['catalog']}]}
        self.assertFalse(learning.decisions(intent,segments,smart=True)['cuts'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'content-library').mkdir()
        (self.root / '08-data').mkdir()
        (self.root / 'content-library/guide.json').write_text(json.dumps({
            '字幕': '教程字幕强调操作参数，关键词来自原句。',
            '节奏': '教程关键步骤放慢，重复口误可以删除，保留必要条件。',
            '声音': '教程声音优先保证人声清晰，配乐降低音量。'}, ensure_ascii=False), encoding='utf-8')
        public_db = self.root / 'content-library/rag/public_dataset_registry.jsonl'
        public_db.parent.mkdir(parents=True)
        dataset = {'id': 'pub-esc50', 'title': 'ESC-50', 'category': 'audio_events_and_sfx',
                   'subcategory': 'environmental_sound_classification', 'source_type': 'research_dataset',
                   'source_url': 'https://github.com/karolpiczak/ESC-50', 'license': 'CC BY-NC 3.0',
                   'license_status': 'non-commercial restriction', 'access_status': 'public_repository',
                   'usage_status': 'research_noncommercial_only', 'summary': '环境音效分类数据集',
                   'fit_for_aicut': '用于音效分类评估', 'limitations': '不可商业训练',
                   'checked_at': '2026-09-29'}
        public_db.write_text(json.dumps(dataset, ensure_ascii=False) + '\n', encoding='utf-8')
        db = self.root / '08-data/memory.sqlite'
        with contextlib.closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('CREATE TABLE preferences(id,rule,evidence,confidence,source)')
            conn.execute("INSERT INTO preferences VALUES(1,'教程字幕不要遮挡手部','人工纠正',.9,'user')")
            conn.execute('CREATE TABLE skills(id,goal,preconditions,verify,success,runs,fails,source_url)')
            conn.execute("INSERT INTO skills VALUES('draft','教程字幕定位','','',0,0,0,'reference')")
        self.bundle = learning.retrieve('教程 字幕 重复 节奏', root=self.root)
        self.segments = [{'start': i * 2., 'end': i * 2. + 2, 'text': text}
                         for i, text in enumerate(['教程先看结果', '重复演示', '操作步骤要清晰', '保留必要条件'])]

    def test_complete_catalog_and_readonly_memory(self):
        db = self.root / '08-data/memory.sqlite'
        before = db.read_bytes()
        data = learning.catalog(self.root)
        self.assertEqual(data['coverage']['preferences'], 1)
        self.assertEqual(data['coverage']['skills'], 1)
        self.assertEqual(before, db.read_bytes())
        skills = [d for d in data['docs'] if d['category'] == 'skills']
        self.assertIn('未验证，禁止执行', skills[0]['text'])

    def test_retrieval_budget_sources_and_unrelated_query(self):
        self.assertTrue(self.bundle['evidence'])
        for d in self.bundle['evidence']:
            self.assertTrue(d['source'])
            self.assertTrue(d['id'])
        result = learning.retrieve('教程字幕', root=self.root, budget=800)
        self.assertLessEqual(sum(len(json.dumps(d, ensure_ascii=False)) for d in result['evidence']), 800)
        self.assertFalse(learning.retrieve('quantum-hyperxyz', root=self.root)['evidence'])

    def test_public_dataset_registry_is_separate_and_retrievable(self):
        data = learning.catalog(self.root)
        self.assertEqual(data['coverage']['public_dataset_audio_events_and_sfx'], 1)
        found = learning.retrieve('ESC-50 音效分类数据集', root=self.root)
        row = next(d for d in found['evidence'] if d['id'] == 'pub-esc50')
        self.assertEqual(row['category'], 'public_dataset')
        self.assertEqual(row['usage_status'], 'research_noncommercial_only')
        self.assertEqual(row['source_url'], 'https://github.com/karolpiczak/ESC-50')

    def test_prompt_context_is_task_shaped_and_filters_catalog_and_unreviewed_examples(self):
        bundle = {'evidence': [
            {'id': 'rule-1', 'category': 'professional_dataset', 'title': '句子完整',
             'text': '保留完整句子边界', 'source': 'guide'},
            {'id': 'dataset-1', 'category': 'public_dataset', 'title': '数据集目录',
             'text': '目录信息，不是编辑规则'},
            {'id': 'candidate-1', 'category': 'account_export',
             'evidence_status': 'automatic_cut_candidate', 'text': '机器候选片段'},
            {'id': 'gold-1', 'category': 'account_export',
             'evidence_status': 'human_reviewed_gold', 'text': '人工确认规则'},
        ]}
        prompt = learning.prompt_context(bundle)
        self.assertIn('rule-1', prompt)
        self.assertIn('gold-1', prompt)
        self.assertNotIn('dataset-1', prompt)
        self.assertNotIn('candidate-1', prompt)
        self.assertIn('自然', prompt)

    def test_visual_memory_reasoning_is_attached_to_edit_intent_prompt(self):
        from aicut import edit_intent
        visual_reasoning = {
            'status': 'analyzed', 'model_saw_images': True,
            'analysis': {'剪辑思考': [{'建议': '按本片句意切换镜头'}]},
        }
        profiles = [{'shot_id': 'shot-1', 't0': 0, 't1': 1,
                     'semantic_evidence': {'image': 'current.jpg',
                                           'status': 'automatic_visual_observation_unreviewed'}}]
        # Build the user-facing prompt through the normal helper directly;
        # this guards the image-grounding instruction and candidate boundary.
        arch = {'arch_id': '-', 'arch_name': '教程', 'layer': 'shortform',
                'segments': [], 'hook': '', 'cta': '', 'density': ''}
        prompt = edit_intent._build_prompt(
            self.segments, None, None, arch, '剪辑镜头', profiles, visual_reasoning)
        self.assertIn('实际查看了当前素材帧与检索样本帧', prompt)
        self.assertIn('不能当成本片镜头', prompt)
        self.assertIn('按本片句意切换镜头', prompt)

    def test_learning_application_requires_real_editable_evidence_and_sentence_binding(self):
        from aicut import edit_intent
        model = self.make_intent()
        ref = 'boundary-rule'
        bundle = {'signature': 'visual-test', 'query': '剪辑 句子切点', 'evidence': [
            {'id': ref, 'category': 'professional_dataset', 'title': '句子完整与切点',
             'text': '以完整句子边界剪辑，保留数字和条件。', 'source': 'fixture'}]}
        model['learning_application'] = {'采用': [
            {'证据id': ref, '对应句号': [2], '具体改变': '保留句子完整边界', '素材依据': '此句含完整步骤'},
            {'证据id': 'fabricated', '对应句号': [2], '具体改变': '删除', '素材依据': '无'},
            {'证据id': ref, '对应句号': [99], '具体改变': '删除', '素材依据': '无'},
        ], '未采用': []}
        ctx = {'arch_id': 'test', 'arch_name': '教程', 'layer': 'shortform',
               'segments': [], 'hook': '', 'cta': '', 'density': ''}
        with patch.object(edit_intent, '_arch_context', return_value=ctx), \
             patch.object(edit_intent, '_build_prompt', return_value='本片逐字稿'), \
             patch.object(edit_intent.plat_cal, 'calibrate_intent', side_effect=lambda x, _: x), \
             patch.object(edit_intent.llm_router, 'llm_chat', return_value=json.dumps(model)):
            result = edit_intent.generate_intent(self.segments, learning_context=bundle)
        self.assertEqual(len(result['learning_application']['采用']), 1)
        self.assertIn('knowledge_applied_semantics', result['_meta'])
        self.assertFalse(result['_meta']['knowledge_applied'])
        self.assertEqual(result['_meta']['knowledge_application_count'], 1)
        self.assertEqual(result['_meta']['knowledge_not_applied_count'], 2)
        self.assertNotIn(ref, result['逐句意图'][1]['知识依据'])

    def test_subtitle_card_requires_source_preserving_executable_phrases(self):
        from aicut import edit_intent
        from aicut.learning_execution import compile_subtitles
        model = self.make_intent()
        ref = 'subtitle-rule'
        sentence = str(self.segments[1]['text'])
        cut = max(1, len(sentence) // 2)
        parts = [sentence[:cut], sentence[cut:]]
        card = {'id': ref, 'category': 'visual_decision_card', 'topic_group': '字幕',
            'title': '字幕语义分句', 'text': '字幕按语义分段',
            'evidence_type': 'human_derived_transferable_rule',
            'evidence_refs': [{'source_url': 'https://example.test/subtitles.webm'}]}
        model['逐句意图'][1]['知识依据'] = [ref]
        model['learning_application'] = {'采用': [{'证据id': ref, '对应句号': [2],
            '具体改变': '字幕按语义分段', '素材依据': '本句包括两个短语',
            '执行参数': {'type': 'subtitle_phrases', 'phrases': parts}}]}
        incomplete = copy.deepcopy(model)
        del incomplete['learning_application']['采用'][0]['执行参数']
        ctx = {'arch_id': 'test', 'arch_name': '教程', 'layer': 'shortform',
               'segments': [], 'hook': '', 'cta': '', 'density': ''}
        with patch.object(edit_intent, '_arch_context', return_value=ctx), \
             patch.object(edit_intent, '_build_prompt', return_value='本片逐字稿'), \
             patch.object(edit_intent.plat_cal, 'calibrate_intent', side_effect=lambda x, _: x), \
             patch.object(edit_intent.llm_router, 'llm_chat',
                          side_effect=[json.dumps(incomplete), json.dumps(model)]) as chat:
            result = edit_intent.generate_intent(self.segments,
                learning_context={'query': '字幕分段', 'evidence': [card]})
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(result['learning_application']['采用'][0]['执行参数']['phrases'], parts)
        compiled, audit = compile_subtitles(result, self.segments)
        self.assertEqual(audit['compiled_count'], 1)
        self.assertEqual(compiled[1]['caption_phrases'], parts)
        self.assertFalse(result['_meta']['knowledge_applied'])

    def test_visual_coverage_gate_accepts_full_scan_and_reports_missing_shot(self):
        from aicut import shotprof
        stream = {'duration': 4.0, 'fps': 2.0,
                  'frames': [{'t': i * .5} for i in range(8)]}
        result = {'duration': 4.0, 'shots': [
            {'shot': 0, 't0': 0, 't1': 2, '_analysis_status': 'analyzed'},
            {'shot': 1, 't0': 2, 't1': 4, '_analysis_status': 'analyzed'},
        ]}
        complete = shotprof.quality_coverage(result, stream)
        self.assertTrue(complete['complete'])
        result['shots'][1]['_analysis_status'] = 'failed'
        result['shots'][1]['_analysis_error'] = 'TimeoutError: local vision request expired'
        incomplete = shotprof.quality_coverage(result, stream)
        self.assertFalse(incomplete['complete'])
        self.assertEqual(incomplete['uncovered_shots'][0]['start'], 2)
        self.assertIn('TimeoutError', incomplete['uncovered_shots'][0]['error'])
        result['shots'][1]['_analysis_status'] = 'analyzed'
        gap_stream = {'duration': 4.0, 'fps': 2.0,
                      'frames': [{'t': 0}, {'t': .5}, {'t': 1.5}, {'t': 2.0},
                                 {'t': 2.5}, {'t': 3.0}, {'t': 3.5}]}
        self.assertFalse(shotprof.quality_coverage(result, gap_stream)['complete'])

    def test_quality_first_rejects_unsigned_legacy_visual_cache(self):
        from aicut import shotprof
        video = self.root / 'same.mp4'
        video.write_bytes(b'same source bytes')
        cache = self.root / 'shotprof-same-structured.json'
        cached = {'video': str(video), 'fingerprint': shotprof.fingerprint(video),
                  'shots': [{'shot': 0, 't0': 0, 't1': 1, '_analysis_status': 'analyzed'}],
                  'duration': 1.0,
                  'visual_coverage': {'complete': True, 'status': 'complete',
                                      'frame_samples': 2, 'expected_frame_samples_min': 2,
                                      'independently_understood_shots': 1, 'shot_count': 1}}
        cache.write_text(json.dumps(cached), encoding='utf-8')
        with patch.object(shotprof, 'WORK', self.root), \
             patch.object(shotprof, 'open_memory'), \
             patch.object(shotprof.vision, 'analyze', side_effect=RuntimeError('unsigned cache must not be reused')):
            with self.assertRaisesRegex(RuntimeError, 'unsigned cache'):
                shotprof.build(video, use_cloud=False, quality_first=True)
        cached['fingerprint'] = 'different-source'
        cache.write_text(json.dumps(cached), encoding='utf-8')
        with patch.object(shotprof, 'WORK', self.root), \
             patch.object(shotprof, 'open_memory'), \
             patch.object(shotprof.vision, 'analyze', side_effect=RuntimeError('cache must not be reused')):
            with self.assertRaisesRegex(RuntimeError, 'must not be reused'):
                shotprof.build(video, use_cloud=False, quality_first=True)

    def test_repeated_run_does_not_reuse_stale_intent_after_prompt_version_change(self):
        spec = importlib.util.spec_from_file_location('cache_auto_edit', ROOT / 'scripts/auto_edit.py')
        auto = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(auto)
        prior_key = learning.cache_key(self.segments, {}, {}, self.bundle,
                                       {'intent_schema': 'old-prompt', 'visual_fingerprint': 'f1'})
        current_key = learning.cache_key(self.segments, {}, {}, self.bundle,
                                         {'intent_schema': 'new-prompt', 'visual_fingerprint': 'f1'})
        cached = {'_learning_cache_key': prior_key, '_meta': {'degraded': False}}
        self.assertTrue(auto.intent_cache_matches(cached, prior_key))
        self.assertFalse(auto.intent_cache_matches(cached, current_key))
        self.assertFalse(auto.intent_cache_matches({'_learning_cache_key': prior_key,
                                                    '_meta': {'degraded': True}}, prior_key))

    def test_refresh_and_cache_identity(self):
        old = self.bundle['signature']
        (self.root / 'content-library/new.md').write_text('教程使用清晰字幕', encoding='utf-8')
        new = learning.retrieve('教程字幕', root=self.root)
        self.assertNotEqual(old, new['signature'])
        self.assertNotEqual(learning.cache_key(self.segments, {}, {}, self.bundle),
                            learning.cache_key(self.segments, {}, {}, new))
        self.assertNotEqual(learning.cache_key(self.segments, {}, {}, new, {'style': 'a'}),
                            learning.cache_key(self.segments, {}, {}, new, {'style': 'b'}))

    def test_missing_database_and_bad_file(self):
        (self.root / 'content-library/bad.json').write_text('{', encoding='utf-8')
        (self.root / '08-data/memory.sqlite').unlink()
        result = learning.retrieve('教程', root=self.root)
        self.assertTrue(result['evidence'])
        self.assertTrue(result['warnings'])

    def test_account_export_learning_index_retrieves_audio_and_review_state(self):
        index = self.root / '08-data/local-datasets/账号导出-20260927/learning-index.jsonl'
        index.parent.mkdir(parents=True)
        rows = [
            {'id': 'audio-1', 'kind': 'audio_event', 'source_sha256': 'a' * 64,
             'source_video': 'D:/account/demo.mp4', 'locator': '2-6',
             'text': '音效敲击清脆 瞬态候选 12', 'evidence_status': 'automatic_audio_feature_unreviewed'},
            {'id': 'audio-2', 'kind': 'audio_event', 'source_sha256': 'e' * 64,
             'source_video': 'D:/account/reviewed.mp4', 'locator': '3-7',
             'text': '人工确认 音效类别 sfx 冲击提示音', 'evidence_status': 'human_reviewed_gold',
             'manual_label': {'status': 'reviewed', 'class': 'sfx', 'reviewer': 'tester'}},
            {'id': 'text-1', 'kind': 'kinetic_text', 'source_sha256': 'b' * 64,
             'source_video': 'D:/account/title.mp4', 'locator': '1.2',
             'text': '花字标题 重点步骤', 'evidence_status': 'human_reviewed_gold'},
            {'id': 'speech-1', 'kind': 'speech_transcript', 'source_sha256': 'c' * 64,
             'source_video': 'D:/account/speech.mp4', 'locator': '1-2',
             'text': '宝宝腹直肌动作步骤讲解', 'evidence_status': 'automatic_asr_unreviewed'},
            {'id': 'ocr-1', 'kind': 'screen_text', 'source_sha256': 'd' * 64,
             'source_video': 'D:/account/ocr.mp4', 'locator': '1.5',
             'text': '宝宝腹直肌重点提示', 'evidence_status': 'automatic_ocr_unreviewed'},
        ]
        index.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
        data = learning.catalog(self.root)
        self.assertEqual(data['coverage']['account_export_audio_event'], 2)
        found = learning.retrieve('音效敲击清脆', root=self.root)
        self.assertTrue(any(row.get('kind') == 'audio_event' and row.get('evidence_status') == 'automatic_audio_feature_unreviewed'
                            for row in found['evidence']))
        reviewed = learning.retrieve('冲击提示音', root=self.root)
        audio_gold = next(row for row in reviewed['evidence'] if row.get('kind') == 'audio_event')
        self.assertEqual(audio_gold.get('evidence_status'), 'human_reviewed_gold')
        self.assertEqual(audio_gold.get('manual_label', {}).get('reviewer'), 'tester')
        self.assertIn('human_reviewed_gold', learning.prompt_context(found))
        mixed = learning.retrieve('宝宝腹直肌动作讲解', root=self.root, top_k=12)
        kinds = {row.get('kind') for row in mixed['evidence'] if row.get('category') == 'account_export'}
        self.assertIn('speech_transcript', kinds)
        self.assertIn('screen_text', kinds)

    def test_router_budget_and_truncated_output(self):
        from aicut import llm_router as router
        backend = router.Backend('test', 'ollama', 'http://127.0.0.1:1', 'test')
        messages = [{'role': 'user', 'content': '教程字幕' * 1400}]
        kwargs = {'images_b64': [], 'json_mode': True, 'temperature': .2,
                  'timeout': 1, 'max_output_tokens': 4096}
        with patch.object(router, '_post_json', return_value={'message': {'content': '{}'}}) as post:
            router._call_ollama(backend, messages, **kwargs)
            options = post.call_args.args[1]['options']
            self.assertEqual(options['num_predict'], 4096)
            self.assertGreater(options['num_ctx'], 8192)
        with patch.object(router, '_post_json', return_value={'message': {'content': '{}'}, 'done_reason': 'length'}):
            with self.assertRaises(ValueError):
                router._call_ollama(backend, messages, **kwargs)

    def test_reordered_sentence_alignment(self):
        intent = {'逐句意图': [{'句号': 1, '句': '先看结果'}, {'句号': 2, '句': '再看步骤'}]}
        result = learning.align_sentences(intent, [{'text': '再看\n步骤'}])
        self.assertEqual(result[0][0], 0)
        self.assertEqual(result[0][1]['句号'], 2)
        self.assertFalse(learning.align_sentences(intent, [{'text': '已经改写的句子'}]))

    def test_rule_fallback_marked_and_sentences_preserved(self):
        from aicut import edit_intent
        ctx = {'arch_id': 'test', 'arch_name': '教程', 'layer': 'shortform',
               'segments': [], 'hook': '', 'cta': '', 'density': ''}
        with patch.object(edit_intent, '_arch_context', return_value=ctx), \
             patch.object(edit_intent, '_build_prompt', return_value='本片逐字稿'), \
             patch.object(edit_intent.plat_cal, 'calibrate_intent', side_effect=lambda x, _: x), \
             patch.object(edit_intent.llm_router, 'llm_chat', side_effect=RuntimeError('offline')), \
             contextlib.redirect_stdout(io.StringIO()):
            result = edit_intent.generate_intent(self.segments, learning_context=self.bundle)
        self.assertTrue(result['_meta']['degraded'])
        self.assertEqual(len(result['逐句意图']), len(self.segments))
        self.assertFalse(learning.decisions(result, self.segments, smart=True)['cuts'])

    def make_intent(self):
        ref = self.bundle['evidence'][0]['id']
        return {'learning_context': self.bundle, 'architecture_decision': {'matched_arch': 'test'},
                '逐句意图': [{'句号': i + 1, '句': s['text'], '动作': '删除',
                             '节奏': '慢', '强调': 'keyframe', '高亮': ['教程', '不存在的词'],
                             '理由': '重复演示', '置信度': .95, '知识依据': [ref]}
                            for i, s in enumerate(self.segments)]}

    def test_decisions_delete_limit_and_grounded_words(self):
        intent = self.make_intent()
        result = learning.decisions(intent, self.segments, smart=True)
        self.assertEqual(len(result['cuts']), 1)
        self.assertEqual(result['keywords'], ['教程'])
        self.assertEqual(result['rhythm']['0.0'], '慢')
        self.assertFalse(learning.decisions(intent, self.segments, smart=False)['cuts'])
        for item in intent['逐句意图']:
            item['知识依据'] = ['invented']
        self.assertFalse(learning.decisions(intent, self.segments, smart=True)['cuts'])

    def test_model_prompt_citations_and_missing_sentences(self):
        from aicut import edit_intent
        model = self.make_intent()
        model['逐句意图'] = model['逐句意图'][:1] + [model['逐句意图'][0]]
        model['逐句意图'][0]['句'] = '伪造的台词'
        model['逐句意图'][0]['知识依据'].append('invented')
        ctx = {'arch_id': 'test', 'arch_name': '教程', 'layer': 'shortform',
               'segments': [], 'hook': '', 'cta': '', 'density': ''}
        with patch.object(edit_intent, '_arch_context', return_value=ctx), \
             patch.object(edit_intent, '_build_prompt', return_value='本片逐字稿'), \
             patch.object(edit_intent.plat_cal, 'calibrate_intent', side_effect=lambda x, _: x), \
             patch.object(edit_intent.llm_router, 'llm_chat', return_value=json.dumps(model)) as call:
            result = edit_intent.generate_intent(self.segments, learning_context=self.bundle)
        prompt = call.call_args.args[0][-1]['content']
        self.assertIn(self.bundle['evidence'][0]['id'], prompt)
        self.assertIn('本片可用的学习依据', prompt)
        self.assertEqual(len(result['逐句意图']), 4)
        self.assertEqual(result['逐句意图'][0]['句'], self.segments[0]['text'])
        self.assertNotIn('invented', result['逐句意图'][0]['知识依据'])
        self.assertEqual(result['逐句意图'][1]['动作'], '保留')

    def test_auto_edit_writes_evidence_and_preserves_intent_keyframes(self):
        spec = importlib.util.spec_from_file_location('audit_auto_edit', ROOT / 'scripts/auto_edit.py')
        auto = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(auto)
        from aicut import edit_intent
        video = self.root / 'source.mp4'
        video.write_bytes(b'test fixture; media probe mocked')
        for directory in ('03-scripts', '02-transcript', 'work'):
            (self.root / directory).mkdir(exist_ok=True)
        intent = self.make_intent()
        for item in intent['逐句意图']:
            item['动作'] = '保留'
        argv = ['auto_edit', '--video', str(video), '--style', 'koubo-zhishi',
                '--name', 'learning-test', '--no-build', '--no-visual-adapter',
                '--keyframes', 'slow-zoom', '--cover', '教程', '--cta', '收藏']
        with patch.object(auto, 'ROOT', self.root), \
             patch.object(auto, 'ffprobe_duration', return_value=8.), \
             patch('aicut.media_integrity.audit_decode', return_value={'complete': True, 'decoded_video_frames': 240}), \
             patch.object(auto, 'load_segments', side_effect=lambda *a: copy.deepcopy(self.segments)), \
             patch.object(auto, 'load_understanding', return_value={}), \
             patch.object(learning, 'retrieve', return_value=self.bundle), \
             patch('aicut.shotprof.build', return_value={
                 'fingerprint': 'test-fingerprint',
                 'visual_coverage': {'complete': True, 'status': 'complete',
                                     'frame_samples': 16, 'expected_frame_samples_min': 15,
                                     'independently_understood_shots': 2, 'shot_count': 2},
                 'summary': {}, 'shots': []}), \
             patch.object(edit_intent, 'generate_intent', return_value=intent), \
             patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            code = auto.main()
        self.assertEqual(code, 0)
        sheet = json.loads((self.root / '03-scripts/learning-test.sheet.json').read_text(encoding='utf-8'))
        self.assertEqual(sheet['learning_context']['signature'], self.bundle['signature'])
        self.assertEqual(len(sheet['learning_decisions']), 4)
        self.assertIn('教程', sheet['keywords'])
        values = [kf['value'] for c in sheet['clips'] for kf in c.get('keyframes', [])]
        self.assertIn(1.14, values)
        self.assertFalse((self.root / '06-drafts').exists())

    def test_auto_edit_retrieval_query_includes_visual_study_terms(self):
        spec = importlib.util.spec_from_file_location('audit_auto_edit_query', ROOT / 'scripts/auto_edit.py')
        auto = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(auto)
        from aicut import edit_intent
        video = self.root / 'source.mp4'
        video.write_bytes(b'test fixture; media probe mocked')
        for directory in ('03-scripts', '02-transcript', 'work'):
            (self.root / directory).mkdir(exist_ok=True)
        intent = self.make_intent()
        captured = {}
        def capture(query, **kwargs):
            captured['query'] = query
            return self.bundle
        argv = ['auto_edit', '--video', str(video), '--style', 'koubo-zhishi',
                '--name', 'visual-query-test', '--no-build', '--no-visual-adapter']
        with patch.object(auto, 'ROOT', self.root), \
             patch.object(auto, 'ffprobe_duration', return_value=8.), \
             patch('aicut.media_integrity.audit_decode', return_value={'complete': True, 'decoded_video_frames': 240}), \
             patch.object(auto, 'load_segments', side_effect=lambda *a: copy.deepcopy(self.segments)), \
             patch.object(auto, 'load_understanding', return_value={}), \
             patch.object(learning, 'retrieve', side_effect=capture), \
             patch('aicut.shotprof.build', return_value={
                 'fingerprint': 'test-fingerprint',
                 'visual_coverage': {'complete': True, 'status': 'complete',
                                     'frame_samples': 16, 'expected_frame_samples_min': 15,
                                     'independently_understood_shots': 2, 'shot_count': 2},
                 'summary': {}, 'shots': []}), \
             patch.object(edit_intent, 'generate_intent', return_value=intent), \
             patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            code = auto.main()
        self.assertEqual(code, 0)
        self.assertIn('本片题材与风格', captured['query'])
        self.assertIn('口播', captured['query'])
        self.assertNotIn('花字视觉剪辑参考', captured['query'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
