"""Host checkpoint invariants. Isolated fixtures, no editor/model operations."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from aicut import host_tasks as host
from aicut.production import atomic_json, read_json


class HostTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = 'QA-new-owned-project'
        self.folder = self.root / self.project
        self.folder.mkdir()
        self.artifact = self.folder / 'draft_content.json'
        atomic_json(self.artifact, {'duration': 2000000, 'fps': 30,
            'canvas_config': {'width': 1080, 'height': 1920},
            'tracks': [{'name': 'main', 'type': 'video', 'segments': [
                {'target_timerange': {'start': 0, 'duration': 2000000}}]}]})
        self.editor = self.root / 'editor' / self.project
        shutil.copytree(self.folder, self.editor)
        self.source = self.root / 'input.mp4'
        self.source.write_bytes(b'isolated fixture input')
        self.sheet = self.root / 'production.sheet.json'
        atomic_json(self.sheet, {'project': self.project, 'clips': []})
        self.shot = self.root / 'observed.png'
        Image.new('RGB', (180, 100), '#334455').save(self.shot)
        self.path = self.root / 'host-task.json'
        self.prepared = {'request_id': 'a' * 32, 'request_sha256': 'b' * 64,
                         'inspection': {'input_versions': [host.binding(self.source)]}}
        self.made = {'artifact': str(self.artifact)}

    def create(self, **kwargs):
        return host.create(self.path, task_id='QA-task', project=self.project,
                           sheet=self.sheet, prepared=self.prepared, made=self.made,
                           editor_project=self.editor, **kwargs)

    def evidence(self, **kwargs):
        return {'project': self.project, 'channel': 'computer-use',
                'observation': 'Isolated UI observation fixture, not real app acceptance',
                'screenshots': [str(self.shot)],
                'output_path': read_json(self.path)['spec']['output'] if self.path.exists() else None, **kwargs}

    def open(self):
        return host.record(self.path, 'open_exact_project', self.evidence())

    def timeline(self):
        with patch.object(host, '_timeline', return_value={'test_fixture': True}):
            return host.record(self.path, 'verify_timeline_and_resources', self.evidence())

    def test_pending_handoff_is_not_execution(self):
        self.create(deliverable='mp4', visible_operations=True)
        task = host.status(self.path)
        self.assertEqual(task['next_step'], 'open_exact_project')
        self.assertFalse(task['native_export_verified'])
        self.assertFalse(task['ready_to_ship'])
        self.assertIn('visible_placement_and_edit', task['steps'])

    def test_ui_cannot_skip_order_or_use_wrong_project_channel_or_missing_shot(self):
        self.create()
        with self.assertRaisesRegex(ValueError, 'preceding'):
            self.timeline()
        for overrides in ({'project': 'OTHER'}, {'channel': 'ffmpeg'}, {'screenshots': []},
                          {'observation': ''}, {'screenshots': [str(self.source)]}):
            with self.subTest(overrides=overrides), self.assertRaises((ValueError, OSError)):
                host.record(self.path, 'open_exact_project', self.evidence(**overrides))
        self.assertFalse(host.status(self.path)['executed_in_editor'])

    def test_restore_resumes_without_replaying_success(self):
        self.create()
        self.open()
        restored = host.status(self.path)
        self.assertEqual(restored['next_step'], 'verify_timeline_and_resources')
        self.assertTrue(self.open()['reused'])
        self.assertEqual(host.status(self.path)['steps']['open_exact_project']['attempts'], 1)

    def test_plan_source_and_project_mutations_invalidate(self):
        for path in (self.sheet, self.source, self.artifact):
            with self.subTest(path=path):
                if self.path.exists():
                    self.path.unlink()
                self.create()
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    host.status(self.path)
                path.write_bytes(original)

    def test_checkpoint_evidence_mutation_invalidates(self):
        self.create()
        self.open()
        self.shot.write_bytes(b'changed evidence')
        with self.assertRaisesRegex(ValueError, 'screenshot changed'):
            host.status(self.path)

    def test_bounded_retries_keep_error_journal(self):
        self.create()
        for _ in range(2):
            task = host.record(self.path, 'open_exact_project', error='Observed UI did not open project')
        self.assertEqual(task['status'], 'blocked')
        self.assertEqual(len(task['events']), 2)
        with self.assertRaisesRegex(ValueError, 'Two attempts'):
            self.open()

    def test_draft_completion_does_not_claim_native_or_visible(self):
        self.create()
        self.open()
        task = self.timeline()
        self.assertTrue(task['executed_in_editor'])
        self.assertFalse(task['native_export_verified'])
        self.assertFalse(task['visible_placement_verified'])
        self.assertFalse(task['ready_to_ship'])
        self.assertIsNone(task['next_step'])

    def test_export_requires_ui_success_then_reserved_new_file(self):
        self.create(deliverable='mp4')
        self.open()
        self.timeline()
        host.record(self.path, 'start_native_export', self.evidence())
        with self.assertRaisesRegex(ValueError, 'observed success'):
            host.record(self.path, 'native_export', self.evidence())
        host.record(self.path, 'native_export', self.evidence(export_success_observed=True))
        with patch.object(host, '_timeline', return_value={}), self.assertRaises(FileNotFoundError):
            host.record(self.path, 'verify_export_media')
        failed = host.status(self.path)
        self.assertEqual(failed['status'], 'blocked')
        self.assertEqual(failed['steps']['verify_export_media']['attempts'], 1)
        output = Path(read_json(self.path)['spec']['output'])
        output.write_bytes(b'isolated output fixture')
        media = {**host.binding(output), 'full_decode_passed': True, 'ready_to_ship': False}
        with patch.object(host, '_timeline', return_value={}), patch.object(host, 'verify_media', return_value=media):
            task = host.record(self.path, 'verify_export_media')
        self.assertTrue(task['native_export_verified'])
        self.assertFalse(task['ready_to_ship'])
        output.write_bytes(b'changed MP4')
        with self.assertRaisesRegex(ValueError, 'export changed'):
            host.status(self.path)

    def test_existing_output_cannot_be_used_as_a_new_native_export(self):
        self.create(deliverable='mp4')
        self.open()
        self.timeline()
        Path(read_json(self.path)['spec']['output']).write_bytes(b'old output')
        with self.assertRaisesRegex(ValueError, 'before export'):
            host.record(self.path, 'start_native_export', self.evidence())
        self.assertEqual(host.status(self.path)['status'], 'blocked')

    def test_stale_output_and_wrong_export_path_rejected(self):
        self.create(deliverable='mp4')
        self.open()
        self.timeline()
        with self.assertRaisesRegex(ValueError, 'reserved'):
            host.record(self.path, 'start_native_export', self.evidence(output_path=str(self.root / 'other.mp4')))
        host.record(self.path, 'start_native_export', self.evidence())
        host.record(self.path, 'native_export', self.evidence(export_success_observed=True))
        output = Path(read_json(self.path)['spec']['output'])
        output.write_bytes(b'stale fixture')
        os.utime(output, (1, 1))
        with patch.object(host, '_timeline', return_value={}), self.assertRaisesRegex(ValueError, 'predates'):
            host.record(self.path, 'verify_export_media')

    def test_changed_saved_copy_is_rechecked_and_failure_invalidates_progress(self):
        self.create()
        self.open()
        saved = self.editor / 'draft_content.json'
        with patch.object(host, '_timeline', return_value={'project_file': host.binding(saved)}):
            host.record(self.path, 'verify_timeline_and_resources', self.evidence())
        saved.write_bytes(saved.read_bytes() + b' ')
        with patch.object(host, '_timeline', side_effect=ValueError('Saved timeline changed')):
            with self.assertRaisesRegex(ValueError, 'Saved timeline changed'):
                host.status(self.path)
        invalidated = read_json(self.path)
        self.assertEqual(invalidated['status'], 'blocked')
        self.assertFalse(invalidated['executed_in_editor'])

    def test_record_also_invalidates_changed_saved_copy_and_updates_reports(self):
        self.create(deliverable='mp4')
        self.open()
        saved = self.editor / 'draft_content.json'
        with patch.object(host, '_timeline', return_value={'project_file': host.binding(saved)}):
            host.record(self.path, 'verify_timeline_and_resources', self.evidence())
        atomic_json(self.root / 'autopilot.json', {'task_id': 'QA-task', 'executed_in_editor': True})
        atomic_json(self.root / 'status.json', {'id': 'QA-task', 'state': 'host_execution_in_progress'})
        saved.write_bytes(b'encrypted or modified saved project')
        with patch.object(host, '_timeline', side_effect=ValueError('Saved project invalid')):
            with self.assertRaisesRegex(ValueError, 'Saved project invalid'):
                host.record(self.path, 'start_native_export', self.evidence())
        self.assertFalse(read_json(self.path)['executed_in_editor'])
        self.assertFalse(read_json(self.root / 'autopilot.json')['executed_in_editor'])
        self.assertEqual(read_json(self.root / 'status.json')['state'], 'blocked')

    def test_original_generated_directory_cannot_impersonate_saved_editor_copy(self):
        self.create()
        with self.assertRaisesRegex(ValueError, 'independent saved editor'):
            host.record(self.path, 'open_exact_project', self.evidence(editor_project=str(self.folder)))
        self.assertFalse(host.status(self.path)['executed_in_editor'])

    def test_encrypted_saved_copy_has_explicit_blocking_reason(self):
        self.create()
        self.open()
        (self.editor / 'draft_content.json').write_bytes(b'not plaintext JSON')
        with self.assertRaisesRegex(ValueError, 'encrypted_saved_draft_needs_host_verification'):
            host.record(self.path, 'verify_timeline_and_resources', self.evidence())
        task = host.status(self.path)
        self.assertEqual(task['status'], 'blocked')
        self.assertFalse(task['executed_in_editor'])

    def test_real_writer_copy_passes_saved_timeline_audit(self):
        # This exercises the actual shared writer + audit, not a mock timeline.
        # The screenshot is explicitly a test fixture; no UI execution is asserted.
        sys.path.insert(0, str(ROOT / 'scripts'))
        import build_draft
        source = self.root / 'real-synthetic.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                        'testsrc2=s=64x96:r=30:d=2', '-c:v', 'libx264', '-preset', 'ultrafast',
                        str(source)], check=True, timeout=45)
        sheet = {'project': 'QA-actual-writer', 'timeline_prepared': True, 'require_audio': False,
                 'width': 360, 'height': 640, 'fps': 30,
                 'clips': [{'file': str(source), 'start': 0, 'end': 2, '特效': '光晕',
                            'effect_params': {'effects_adjust_background_animation': 20}}]}
        sheet_path = self.root / 'actual.sheet.json'
        atomic_json(sheet_path, sheet)
        generated = build_draft.build(sheet, build_draft.load_config(), output_root=self.root / 'new-drafts')
        editor = self.root / 'new-editor' / sheet['project']
        shutil.copytree(generated, editor)
        task_path = self.root / 'actual-host-task.json'
        host.create(task_path, task_id='QA-actual', project=sheet['project'], sheet=sheet_path,
                    prepared=self.prepared, made={'artifact': str(generated / 'draft_content.json')},
                    editor_project=editor)
        evidence = {'project': sheet['project'], 'channel': 'computer-use',
                    'observation': 'Synthetic protocol fixture only, no real UI', 'screenshots': [str(self.shot)]}
        host.record(task_path, 'open_exact_project', evidence)
        task = host.record(task_path, 'verify_timeline_and_resources', evidence)
        self.assertEqual(task['steps']['verify_timeline_and_resources']['evidence']['timeline']['execution_audit']['status'], 'complete')
        self.assertFalse(task['native_export_verified'])

    def test_status_repairs_autopilot_and_job_views(self):
        self.create()
        atomic_json(self.root / 'autopilot.json', {'task_id': 'QA-task',
                    'status': 'project_generated', 'production': {'status': 'project_generated'}})
        atomic_json(self.root / 'status.json', {'id': 'QA-task', 'state': 'project_generated', 'finished_at': 1})
        self.open()
        task = self.timeline()
        result = read_json(self.root / 'autopilot.json')
        self.assertEqual(result['status'], task['status'])
        self.assertEqual(result['production_status'], 'project_generated')
        self.assertEqual(read_json(self.root / 'status.json')['generation_finished_at'], 1)
        self.assertNotIn('finished_at', read_json(self.root / 'status.json'))
        with self.assertRaises(ValueError):
            host.record(self.path, 'open_exact_project', error='cannot undo verified checkpoint')

    def test_legacy_task_cannot_silently_become_verified(self):
        atomic_json(self.path, {'schema': 'aicut-host-task-1'})
        with self.assertRaisesRegex(ValueError, 'Legacy'):
            host.status(self.path)


class MediaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.video = Path(temp.name) / 'video.mp4'
        self.video.write_bytes(b'media fixture for subprocess validation')
        self.expected = {'duration': 2, 'width': 1080, 'height': 1920, 'fps': 30, 'require_audio': True}
        self.probe = {'format': {'duration': '2.04'}, 'streams': [
            {'codec_type': 'video', 'width': 1080, 'height': 1920, 'avg_frame_rate': '30/1'},
            {'codec_type': 'audio'}]}

    def run_check(self, decode_code=0):
        result = [subprocess.CompletedProcess([], 0, json.dumps(self.probe), ''),
                  subprocess.CompletedProcess([], decode_code, '', 'decode fixture error' if decode_code else '')]
        with patch.object(host.subprocess, 'run', side_effect=result):
            return host.verify_media(self.video, self.expected)

    def test_technical_success_leaves_aesthetic_pending(self):
        checked = self.run_check()
        self.assertTrue(checked['full_decode_passed'])
        self.assertEqual(checked['visual_review'], 'pending')
        self.assertFalse(checked['ready_to_ship'])

    def test_wrong_dimensions_fps_duration_audio_and_corrupt_decode_rejected(self):
        for field, value in [('width', 720), ('avg_frame_rate', '25/1')]:
            old = self.probe['streams'][0][field]
            self.probe['streams'][0][field] = value
            with self.assertRaises(ValueError):
                self.run_check()
            self.probe['streams'][0][field] = old
        self.probe['format']['duration'] = 'nan'
        with self.assertRaises(ValueError):
            self.run_check()
        self.probe['format']['duration'] = '2.04'
        self.probe['streams'].pop()
        with self.assertRaisesRegex(ValueError, 'audio'):
            self.run_check()
        self.expected['require_audio'] = False
        with self.assertRaisesRegex(ValueError, 'decode'):
            self.run_check(decode_code=1)
        self.assertFalse(self.run_check()['audio_present'])


if __name__ == '__main__':
    unittest.main()
