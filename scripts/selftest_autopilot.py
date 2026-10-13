"""Coordinator invariants. Isolated temp jobs; no model or editor UI calls."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aicut import autopilot


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.mp4'
        self.source.write_bytes(b'isolated media fixture')
        self.sheet = self.root / 'original.sheet.json'
        self.original = {'project': 'USER-EXISTING', 'clips': [
            {'file': str(self.source), 'start': 0, 'end': 2}],
            'production_health': {'intent_requested': True, 'intent_available': True}}
        self.sheet.write_text(json.dumps(self.original), encoding='utf-8')
        self.root_patch = patch.object(autopilot, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.api_patch = patch.object(autopilot, 'MakerAPI')
        self.api = self.api_patch.start().return_value
        self.addCleanup(self.api_patch.stop)
        self.api.prepare.return_value = {'ok': True, 'request_id': 'a' * 32,
            'request_sha256': 'b' * 64, 'selection': {'software': 'jianying'}}
        self.api.execute.return_value = {'ok': True,
            'status': 'project_generated_pending_host_verification',
            'artifact': str(self.root / 'generated' / 'draft_content.json')}
        artifact = self.root / 'generated' / 'draft_content.json'
        artifact.parent.mkdir()
        artifact.write_text(json.dumps({'duration': 2000000, 'fps': 30,
            'canvas_config': {'width': 1080, 'height': 1920},
            'tracks': [{'name': 'main', 'type': 'video', 'segments': [
                {'target_timerange': {'start': 0, 'duration': 2000000}}]}]}))

    def run_sheet(self, **kwargs):
        return autopilot.run(goal='保留原声并按台词分段剪辑', sheet=self.sheet, **kwargs)

    def test_preview_does_not_execute(self):
        result = self.run_sheet()
        self.assertEqual(result['status'], 'awaiting_confirmation')
        self.api.execute.assert_not_called()

    def test_local_authorization_is_bound_and_recorded(self):
        result = self.run_sheet(execute=True)
        self.api.execute.assert_called_once_with('a' * 32, approved_request_sha256='b' * 64)
        auth = json.loads((Path(result['report_path']).parent / 'authorization.json').read_text(encoding='utf-8'))
        self.assertEqual(auth['request_sha256'], 'b' * 64)
        self.assertEqual(auth['goal'], result['goal'])

    def test_native_completion_never_inferred_from_draft(self):
        result = self.run_sheet(execute=True)
        host = json.loads(Path(result['host_task']).read_text())
        self.assertFalse(result['executed_in_editor'])
        self.assertFalse(result['ready_to_ship'])
        self.assertFalse(host['native_export_verified'])
        self.assertEqual(host['status'], 'pending_host_execution')
        self.assertEqual(result['host_next_step'], 'open_exact_project')
        self.assertEqual(result['status'], 'pending_host_execution')
        self.assertEqual(host['spec']['deliverable'], 'draft')

    def test_existing_project_and_sheet_not_overwritten(self):
        result = self.run_sheet(execute=True)
        owned = json.loads(Path(result['sheet']).read_text())
        self.assertNotEqual(owned['project'], 'USER-EXISTING')
        self.assertEqual(json.loads(self.sheet.read_text()), self.original)

    def test_degraded_model_blocks_before_prepare(self):
        self.original['production_health']['intent_degraded'] = True
        self.sheet.write_text(json.dumps(self.original))
        result = self.run_sheet(execute=True)
        self.assertFalse(result['ok'])
        self.api.prepare.assert_not_called()

    def test_preflight_failure_never_executes(self):
        self.api.prepare.return_value = {'ok': False, 'error': 'unsupported resource'}
        result = self.run_sheet(execute=True)
        self.assertEqual(result['status'], 'blocked')
        self.api.execute.assert_not_called()

    def test_pipeline_failure_cannot_reuse_old_sheet(self):
        with patch.object(autopilot, 'config', return_value={'ai_router': {'global_paid_api_enabled': False}}), \
             patch.object(autopilot.Job, 'command', side_effect=RuntimeError('model down')):
            result = autopilot.run(goal='新素材自动剪辑', material=self.source, execute=True)
        self.assertFalse(result['ok'])
        self.api.prepare.assert_not_called()

    def test_native_and_invalid_copy_mode_rejected(self):
        with self.assertRaises(ValueError):
            self.run_sheet(execute=True, mode='native')
        with self.assertRaises(ValueError):
            self.run_sheet(execute=True, mode='mp4', copy_to_editor=True)
        with self.assertRaises(ValueError):
            self.run_sheet(execute=True, mode='mp4', visible_operations=True)

    def test_native_mp4_and_visible_steps_are_explicit(self):
        result = self.run_sheet(execute=True, host_deliverable='mp4', visible_operations=True)
        host = json.loads(Path(result['host_task']).read_text())
        self.assertIn('visible_placement_and_edit', host['steps'])
        self.assertIn('verify_export_media', host['steps'])
        self.assertFalse(host['native_export_verified'])

    def test_local_workflow_will_not_activate_paid_fallback(self):
        with patch.object(autopilot, 'config', return_value={'ai_router': {'global_paid_api_enabled': True}}):
            with self.assertRaisesRegex(ValueError, 'paid API'):
                autopilot.run(goal='剪辑', material=self.source, execute=True)
        self.api.prepare.assert_not_called()


    def test_material_branch_consumes_only_current_pipeline_plan(self):
        observed = []
        def finished_pipeline(job, command, root):
            observed.append(command)
            pipeline_id = command[command.index('--task-id') + 1]
            folder = self.root / 'work' / 'tasks' / pipeline_id
            folder.mkdir(parents=True)
            current = folder / 'current.sheet.json'
            current.write_text(json.dumps(self.original))
            (folder / 'status.json').write_text(json.dumps({
                'state': 'planned', 'warnings': [],
                'artifacts': {'working_sheet': str(current)}}))
        with patch.object(autopilot, 'config', return_value={'ai_router': {'global_paid_api_enabled': False}}), \
             patch.object(autopilot.Job, 'command', finished_pipeline):
            result = autopilot.run(goal='按台词剪辑', material=self.source, execute=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['plan_origin'], 'brain_pipeline')
        self.assertIn('--smart', observed[0])
        self.assertIn('--intent', observed[0])
        self.assertIn('--no-build', observed[0])
        self.assertEqual(observed[0][observed[0].index('--goal') + 1], '按台词剪辑')


if __name__ == '__main__':
    unittest.main()
