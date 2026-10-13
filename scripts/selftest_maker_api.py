"""Production interface + agent routing tests. No model calls, editor UI or knowledge writes."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'scripts')]
from aicut.maker_api import MakerAPI, tool_definitions
from aicut import plan, skills, skills_maker
from aicut.agent import Agent
from aicut.mcp_server import McpServer
import deliver_edit


class MakerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='aicut-maker-api-')
        cls.root = Path(cls.tmp.name)
        cls.source = cls.root / 'source.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=s=240x426:r=15:d=3', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=3', '-shortest', '-c:v', 'libx264',
                        '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                        str(cls.source)], check=True, timeout=60)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.case = self.root / self._testMethodName
        self.case.mkdir()
        self.api = MakerAPI(self.case / 'requests')
        self.sheet = self.case / 'sheet.json'
        self.data = {'project': 'qa-maker-' + self._testMethodName.replace('test_', '')[:30],
                     'width': 240, 'height': 426, 'fps': 15, 'timeline_prepared': True,
                     'requires_plan_confirmation': True,
                     'clips': [{'file': str(self.source), 'start': 0, 'end': 3,
                                'speed': 1.5, 'volume': .2,
                                'keyframes': [{'property': 'scale', 't': 0, 'value': 1},
                                              {'property': 'scale', 't': 2, 'value': 1.1}]}]}
        self.sheet.write_text(json.dumps(self.data), encoding='utf-8')

    def prepare(self, **kwargs):
        return self.api.prepare(self.sheet, **kwargs)

    def test_inspection_is_not_rendering(self):
        with patch.object(deliver_edit, 'deliver') as execute:
            result = self.prepare()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['status'], 'awaiting_confirmation')
        self.assertEqual(len(result['request_sha256']), 64)
        execute.assert_not_called()

    def test_single_line_caption_setting_reaches_native_writer(self):
        from aicut.text_overlays import split_caption_timeline
        text = '有什么不同的地方可以在评论区留言'
        subs = self.case / 'subtitles.json'
        rows = [{'start': 0, 'end': 2, 'text': text}]
        subs.write_text(json.dumps({'segments': rows}, ensure_ascii=False), encoding='utf-8')
        self.data.update(subtitles_json=str(subs),
                         subtitle_layout={'single_line_per_caption': True, 'max_chars_per_line': 12})
        self.sheet.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        request = self.prepare()
        result = self.api.execute(request['request_id'], approved_request_sha256=request['request_sha256'])
        self.assertTrue(result['ok'], result)
        draft = json.loads(Path(result['artifact']).read_text(encoding='utf-8'))
        track = next(t for t in draft['tracks'] if t['name'] == 'subtitle')
        expected = split_caption_timeline(rows, max_chars=12)
        self.assertEqual(len(track['segments']), len(expected))
        self.assertGreater(len(expected), 1)
        for item, event in zip(track['segments'], expected):
            self.assertAlmostEqual(item['target_timerange']['start'] / 1e6, event['start'], places=3)
            self.assertAlmostEqual(item['target_timerange']['duration'] / 1e6, event['end'] - event['start'], places=3)

    def test_semantic_caption_parts_reach_native_text_materials(self):
        from aicut.text_overlays import split_caption_timeline
        parts = ['前面给大家讲了', '是宝宝饮食方面的', '需要准备的一些东西']
        rows = [{'start': 0, 'end': 2, 'text': ''.join(parts), 'caption_phrases': parts}]
        subs = self.case / 'semantic.json'
        subs.write_text(json.dumps({'segments': rows}, ensure_ascii=False), encoding='utf-8')
        self.data.update(subtitles_json=str(subs),
            subtitle_layout={'single_line_per_caption': True, 'max_chars_per_line': 12})
        self.sheet.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        request = self.prepare()
        result = self.api.execute(request['request_id'], approved_request_sha256=request['request_sha256'])
        self.assertTrue(result['ok'], result)
        draft = json.loads(Path(result['artifact']).read_text(encoding='utf-8'))
        materials = {m['id']: json.loads(m['content'])['text'] for m in draft['materials']['texts']}
        track = next(t for t in draft['tracks'] if t['name'] == 'subtitle')
        self.assertEqual([materials[s['material_id']] for s in track['segments']], parts)
        for segment, event in zip(track['segments'], split_caption_timeline(rows)):
            self.assertAlmostEqual(segment['target_timerange']['start'] / 1e6, event['start'], places=3)

    def test_model_cannot_supply_its_own_approval(self):
        result = self.prepare()
        with self.assertRaisesRegex(ValueError, 'application'):
            self.api.call_tool('make_video', {'request_id': result['request_id'],
                                               'approved_request_sha256': result['request_sha256']})
        with self.assertRaisesRegex(ValueError, 'confirmation'):
            self.api.call_tool('make_video', {'request_id': result['request_id']})

    def test_changed_plan_rejected_before_execution(self):
        result = self.prepare()
        self.data['clips'][0]['volume'] = .9
        self.sheet.write_text(json.dumps(self.data), encoding='utf-8')
        with patch.object(deliver_edit, 'deliver') as execute:
            with self.assertRaisesRegex(ValueError, 'Plan changed'):
                self.api.execute(result['request_id'], approved_request_sha256=result['request_sha256'])
        execute.assert_not_called()

    def test_destination_changes_invalidate_approval(self):
        result = self.prepare()
        request_path = self.api.root / result['request_id'] / 'request.json'
        saved = json.loads(request_path.read_text(encoding='utf-8'))
        saved['request']['publish'] = True
        request_path.write_text(json.dumps(saved), encoding='utf-8')
        with patch.object(deliver_edit, 'deliver') as execute:
            with self.assertRaisesRegex(ValueError, 'approved or was changed'):
                self.api.execute(result['request_id'], approved_request_sha256=result['request_sha256'])
        execute.assert_not_called()

    def test_snapshot_tamper_is_rejected(self):
        result = self.prepare()
        (self.api.root / result['request_id'] / 'plan.sheet.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Plan changed'):
            self.api.execute(result['request_id'], approved_request_sha256=result['request_sha256'])

    def test_changed_subtitle_reference_rejected_before_execution(self):
        subtitles = self.case / 'captions.json'
        subtitles.write_text(json.dumps({'segments': [{'start': 0, 'end': 2, 'text': 'original'}]}), encoding='utf-8')
        self.data['subtitles_json'] = str(subtitles)
        self.sheet.write_text(json.dumps(self.data), encoding='utf-8')
        result = self.prepare()
        subtitles.write_text(json.dumps({'segments': [{'start': 0, 'end': 2, 'text': 'changed'}]}), encoding='utf-8')
        with patch.object(deliver_edit, 'deliver') as execute:
            with self.assertRaisesRegex(ValueError, 'Input changed'):
                self.api.execute(result['request_id'], approved_request_sha256=result['request_sha256'])
        execute.assert_not_called()

    def test_changed_media_reference_rejected_before_execution(self):
        media = self.case / 'source.mp4'
        media.write_bytes(self.source.read_bytes())
        self.data['clips'][0]['file'] = str(media)
        self.sheet.write_text(json.dumps(self.data), encoding='utf-8')
        result = self.prepare()
        with media.open('ab') as stream:
            stream.write(b'changed')
        with patch.object(deliver_edit, 'deliver') as execute:
            with self.assertRaisesRegex(ValueError, 'Input changed'):
                self.api.execute(result['request_id'], approved_request_sha256=result['request_sha256'])
        execute.assert_not_called()

    def test_native_export_stays_blocked(self):
        self.assertFalse(self.prepare(engine='native')['ok'])
        self.assertFalse(self.prepare(software='premiere')['ok'])
        self.assertFalse(self.api.root.exists())

    def test_invalid_output_and_request_ids(self):
        with self.assertRaises(ValueError):
            self.prepare(engine='project', output='ignored.mp4')
        with self.assertRaises(ValueError):
            self.prepare(engine='portable', output='wrong.mov')
        for bad in ('../request', '', 'a' * 31, None):
            with self.assertRaises(ValueError):
                self.api.execute(bad, approved_request_sha256='anything')

    def test_real_mp4_and_idempotent_retry(self):
        output = self.case / 'video.mp4'
        prepared = self.prepare(engine='portable', output=output)
        approvals = {prepared['request_id']: prepared['request_sha256']}
        result = self.api.call_tool('make_video', {'request_id': prepared['request_id']}, approvals=approvals)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['status'], 'rendered_pending_visual_review')
        self.assertTrue(result['delivery']['media_validation']['full_decode_passed'])
        self.assertFalse(result['delivery']['executed_in_editor'])
        with patch.object(deliver_edit, 'deliver') as execute:
            repeated = self.api.call_tool('make_video', {'request_id': prepared['request_id']}, approvals=approvals)
        execute.assert_not_called()
        self.assertTrue(repeated['reused'])
        output.write_bytes(b'changed output')
        with self.assertRaisesRegex(ValueError, 'changed or disappeared'):
            self.api.call_tool('make_video', {'request_id': prepared['request_id']}, approvals=approvals)

    def test_real_native_draft_uses_existing_writer(self):
        import build_draft
        prepared = self.prepare()
        original = build_draft.build
        with patch.object(build_draft, 'build', side_effect=lambda s, c: original(s, c, output_root=self.case / 'drafts')):
            result = self.api.execute(prepared['request_id'], approved_request_sha256=prepared['request_sha256'])
        self.assertTrue(result['ok'], result)
        draft = json.loads(Path(result['artifact']).read_text(encoding='utf-8'))
        self.assertEqual(draft['duration'], 2_000_000)
        self.assertTrue(next(t for t in draft['tracks'] if t['name'] == 'main')['segments'][0]['common_keyframes'])
        self.assertFalse(result['delivery']['host_render_verified'])

    def test_planner_sees_tools_without_approval_secret(self):
        self.assertIn('make_video', skills.REGISTRY)
        self.assertIn('inspect_video_plan', skills.REGISTRY)
        state = {'production': {'sheet': str(self.sheet), 'software': 'jianying', 'engine': 'project'}}
        chosen = plan.plan_rules('按方案制作视频', state)['步骤'][0]
        self.assertEqual(chosen['skill'], 'inspect_video_plan')
        state['production'] = {'request_id': 'a' * 32, 'user_approved': True}
        self.assertEqual(plan.plan_rules('制作视频', state)['步骤'][0]['skill'], 'make_video')
        make_schema = tool_definitions()[1]['function']['parameters']
        self.assertEqual(set(make_schema['properties']), {'request_id'})
        self.assertIn(str(self.source), plan.state_brief({'video': str(self.source)}))

    def test_prepared_production_request_does_not_require_another_model_call(self):
        state = {'production': {'request_id': 'a' * 32, 'user_approved': True}}
        with patch.object(plan.llm, 'json_chat') as model:
            result = plan.plan_any('执行已确认请求', state)
        model.assert_not_called()
        self.assertEqual(result['步骤'][0]['skill'], 'make_video')
        self.assertEqual(result['来源'], 'rules:production-api')

    def test_failed_planning_cannot_reuse_old_draft(self):
        ctx = skills.Context(video=self.source, goal='保留原话')
        proc = SimpleNamespace(returncode=1, stdout='', stderr='model connection failed')
        with patch.object(skills.subprocess, 'run', return_value=proc), \
             patch('aicut.maker_api.MakerAPI.prepare') as prepare:
            result = skills.skill_generate_draft(ctx, {})
        self.assertFalse(result['ok'])
        self.assertNotIn('result_draft', result)
        prepare.assert_not_called()

    def test_material_planning_calls_interface_without_building(self):
        output = self.case / 'chosen.mp4'
        ctx = skills.Context(video=self.source, goal='保留原话，按短语排字幕',
                             production_options={'software': 'premiere', 'engine': 'portable',
                                                 'output': str(output), 'publish': False})
        original_run = skills.subprocess.run
        def subprocess_result(command, **kwargs):
            if not (len(command) > 1 and str(command[1]).endswith('pipeline.py')):
                return original_run(command, **kwargs)
            self.assertTrue(command[1].endswith('pipeline.py'))
            self.assertIn('--no-build', command)
            self.assertNotIn('--publish', command)
            self.assertEqual(command[command.index('--goal') + 1], ctx.goal)
            tid = command[command.index('--task-id') + 1]
            status = self.case / 'tasks' / tid / 'status.json'
            status.parent.mkdir(parents=True)
            status.write_text(json.dumps({'state': 'planned', 'artifacts': {'working_sheet': str(self.sheet)}}), encoding='utf-8')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        with patch.object(skills, 'WORK', self.case), \
             patch.object(skills.subprocess, 'run', side_effect=subprocess_result), \
             patch('aicut.maker_api.MakerAPI', return_value=self.api):
            result = skills.skill_generate_draft(ctx, {})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['status'], 'awaiting_confirmation')
        self.assertTrue(result['stop_agent'])
        self.assertEqual(result['selection']['software'], 'premiere')
        self.assertEqual(result['selection']['engine'], 'portable')
        self.assertEqual(result['selection']['output'], str(output))

    def test_agent_dispatch_uses_application_approval(self):
        prepared = self.prepare()
        ctx = skills.Context()
        with patch.object(skills_maker, 'MakerAPI', return_value=self.api):
            blocked = skills_maker.run_tool(ctx, 'make_video', {'request_id': prepared['request_id']})
        self.assertFalse(blocked['ok'])
        self.assertEqual(blocked['status'], 'blocked')

    def test_agent_stops_after_inspection(self):
        fake_memory = MagicMock()
        steps = [{'skill': 'inspect_video_plan', 'args': {'sheet': str(self.sheet), 'software': 'jianying', 'engine': 'project'}},
                 {'skill': 'make_video', 'args': {'request_id': 'invented'}}]
        with patch('aicut.agent.open_memory', return_value=fake_memory), \
             patch.object(Agent, 'perceive', return_value={}), \
             patch('aicut.agent.plan_mod.plan_any', return_value={'步骤': steps, '理解': '检查方案', '来源': 'test-double'}), \
             patch('aicut.export_report.ExportReport'), \
             patch('aicut.brain_logger.log_decision'), \
             patch.object(skills_maker, 'MakerAPI', return_value=self.api):
            agent = Agent(goal='检查方案', use_vision=False, log=lambda _: None)
            result = agent.run()
        self.assertEqual(len(result['steps']), 1)
        self.assertEqual(result['status'], 'awaiting_confirmation')
        self.assertEqual(result['stop_reason'], 'awaiting_confirmation')
        self.assertFalse(result['ready_to_ship'])
        self.assertIn('request_sha256', result['production'])

    def test_agent_dry_run_does_not_prepare_or_render(self):
        agent = Agent.__new__(Agent)
        agent.dry_run, agent.draft, agent.target = True, None, None
        agent.goal, agent.video, agent.production = '检查方案', None, {}
        agent.state, agent.task, agent.retries = {}, 'qa-dry-run', 0
        agent._export_report, agent.use_vision = None, False
        agent.mem, agent.log, agent.steps = MagicMock(), lambda _: None, []
        with patch.object(skills_maker, 'MakerAPI') as api:
            result = agent._execute_step({'skill': 'inspect_video_plan', 'args': {
                'sheet': str(self.sheet), 'software': 'jianying', 'engine': 'project'}})
        api.assert_not_called()
        self.assertTrue(result.ok)
        self.assertTrue(result.report['dry_run'])

    def test_mcp_catalog_includes_typed_production_tools(self):
        server = McpServer()
        definitions = server.handle({'id': 1, 'method': 'tools/list'})['result']['tools']
        index = {t['name']: t['inputSchema'] for t in definitions}
        self.assertEqual(index['inspect_video_plan']['properties']['publish']['type'], 'boolean')
        self.assertEqual(set(index['make_video']['properties']), {'request_id', 'dry_run'})

    def test_mcp_dry_run_does_not_call_interface(self):
        with patch.object(skills_maker, 'MakerAPI') as api:
            result = McpServer().state.call_skill('inspect_video_plan', {
                'sheet': str(self.sheet), 'software': 'jianying', 'engine': 'project', 'dry_run': True})
        api.assert_not_called()
        self.assertEqual(result['status'], 'dry_run')

    def test_mcp_execution_uses_application_approval_and_clean_stdout(self):
        import io
        from contextlib import redirect_stdout
        prepared = self.prepare(engine='portable', output=self.case / 'mcp-video.mp4')
        server = McpServer(production_approvals={prepared['request_id']: prepared['request_sha256']})
        stdout = io.StringIO()
        with patch.object(skills_maker, 'MakerAPI', return_value=self.api), redirect_stdout(stdout):
            result = server.handle({'id': 1, 'method': 'tools/call', 'params': {
                'name': 'make_video', 'arguments': {'request_id': prepared['request_id']}}})
        self.assertEqual(stdout.getvalue(), '')
        self.assertFalse(result['result']['isError'], result)
        report = json.loads(result['result']['content'][0]['text'])
        self.assertTrue(Path(report['artifact']).is_file())
        self.assertEqual(report['status'], 'rendered_pending_visual_review')

    def test_mcp_missing_application_approval_rejects_execution(self):
        prepared = self.prepare()
        with patch.object(skills_maker, 'MakerAPI', return_value=self.api):
            result = McpServer().state.call_skill('make_video', {'request_id': prepared['request_id']})
        self.assertFalse(result['ok'])
        self.assertIn('confirmation', result['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
