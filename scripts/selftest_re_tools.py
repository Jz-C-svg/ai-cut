"""Offline RE/readback invariants. No model, editor UI or DLL invocation."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT / 'scripts'))
from aicut import native_draft as native, re_probe as probe, host_tasks as host
from aicut.production import atomic_json


class ReTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / 'editor' / 'owned-project'
        self.project.mkdir(parents=True)
        self.file = self.project / 'draft_content.json'
        self.data = {'duration': 2000000, 'fps': 30, 'canvas_config': {'width': 1080, 'height': 1920},
                     'tracks': [{'type': 'video', 'segments': [
                         {'target_timerange': {'start': 0, 'duration': 2000000}}]}]}
        self.original = json.dumps(self.data).encode()
        self.file.write_bytes(self.original)

    def test_json_bom_and_empty_template(self):
        self.file.write_bytes(b'\xef\xbb\xbf' + self.original)
        self.assertEqual(probe.probe_file(self.file)['kind'], 'json_project')
        atomic_json(self.file, {'duration': 0, 'tracks': []})
        self.assertEqual(probe.probe_file(self.file)['kind'], 'json_empty_template')

    def test_malformed_is_not_opaque(self):
        self.file.write_bytes(b'{"tracks":')
        self.assertEqual(probe.probe_file(self.file)['kind'], 'malformed_json')
        self.file.write_bytes(b'A' * 64)
        self.assertEqual(probe.probe_file(self.file)['kind'], 'opaque_base64_like')
        self.file.write_bytes(b'\x00\xff\x80')
        self.assertEqual(probe.probe_file(self.file)['kind'], 'unknown_binary')

    def test_readonly_does_not_recover_tmp_or_read_credentials(self):
        self.file.write_bytes(b'opaque not-json')
        tmp = self.file.with_name(self.file.name + '.tmp')
        tmp.write_bytes(self.original)
        secret = self.project / 'crypto_key_store.dat'
        secret.write_text('DO_NOT_READ')
        before = {p.name: p.read_bytes() for p in self.project.iterdir()}
        report = probe.probe_project(self.project)
        self.assertFalse(any('DO_NOT_READ' in str(x) for x in report['files']))
        self.assertFalse(any('crypto_key_store' in x['path'] for x in report['files']))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.project.iterdir()})

    def test_oversized_stream_hash_without_json_parse(self):
        with patch.object(probe, 'LIMIT', 8):
            result = probe.probe_file(self.file)
        self.assertEqual(result['kind'], 'too_large_to_parse')
        self.assertEqual(result['sha256'], native.sha256(self.file))

    def test_report_cannot_write_into_project(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            probe.write_report(probe.probe_project(self.project), self.project / 'report.json')

    def test_duplicate_hashes_are_not_selected_as_authoritative(self):
        (self.project / 'draft_content.json.bak').write_bytes(self.original)
        report = probe.probe_project(self.project)
        self.assertEqual(len(report['duplicate_groups']), 1)
        self.assertFalse(report['executed_in_editor'])
        self.assertFalse(report['native_export_verified'])

    def test_symlink_input_is_skipped(self):
        outside = self.root / 'outside.json'
        outside.write_bytes(self.original)
        self.file.unlink()
        try:
            self.file.symlink_to(outside)
        except OSError:
            self.skipTest('Host lacks symlink privilege')
        self.assertEqual(probe.probe_file(self.file)['kind'], 'linked_input_skipped')
        with self.assertRaisesRegex(ValueError, 'links'):
            native.snapshot(self.project, self.root / 'out')

    def test_static_pe_does_not_execute_binary(self):
        with patch('subprocess.run', side_effect=AssertionError('Must be static')):
            result = probe.inspect_pe(sys.executable)
        self.assertEqual(result['tool'], 'pefile')
        self.assertEqual(result['machine'], '0x8664')
        self.assertFalse(result['target_executed'])

    def test_plain_snapshot_preserves_original_bytes(self):
        report = native.snapshot(self.project, self.root / 'out')
        native.validate_snapshot(report)
        self.assertEqual(report['files'][0]['method'], 'plain_json')
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertFalse(report['executed_in_editor'])
        self.assertFalse(report['native_export_verified'])

    def test_snapshot_cannot_write_into_project(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            native.snapshot(self.project, self.project / 'outputs')

    def test_malformed_json_never_invokes_native_decoder(self):
        self.file.write_bytes(b'{truncated')
        with patch.object(native, 'decode_file', side_effect=AssertionError('Not encryption')):
            with self.assertRaisesRegex(ValueError, 'malformed'):
                native.snapshot(self.project, self.root / 'out')

    def test_codec_hash_change_blocks_before_loading(self):
        dll = self.root / 'videoeditor.dll'
        dll.write_bytes(b'changed test fixture')
        config = self.root / 'codec.json'
        atomic_json(config, {'schema': 'aicut-native-codec-1', 'mode': 'read_only',
                             'dll': str(dll), 'dll_sha256': '0' * 64})
        with self.assertRaisesRegex(ValueError, 'version changed'):
            native.pinned_codec(config)

    def decoded_fixture(self, source, output, **kwargs):
        Path(output).write_bytes(self.original)
        return {'ok': True}

    def test_opaque_snapshot_provenance(self):
        self.file.write_bytes(b'A' * 64)
        with patch.object(native, 'pinned_codec', return_value=None), \
                patch.object(native, 'decode_file', side_effect=self.decoded_fixture):
            result = native.snapshot(self.project, self.root / 'out')
        self.assertEqual(result['files'][0]['method'], 'pinned_native_codec')
        self.assertEqual(self.file.read_bytes(), b'A' * 64)
        native.validate_snapshot(result)

    def test_original_or_decoded_mutation_invalidates(self):
        report = native.snapshot(self.project, self.root / 'out')
        Path(report['files'][0]['snapshot']).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'changed'):
            native.validate_snapshot(report)

    def test_source_changed_during_decoding_is_rejected(self):
        self.file.write_bytes(b'A' * 64)
        def mutate(*args, **kwargs):
            self.decoded_fixture(*args, **kwargs)
            self.file.write_bytes(b'B' * 64)
        with patch.object(native, 'pinned_codec', return_value=None), \
                patch.object(native, 'decode_file', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'changed'):
                native.snapshot(self.project, self.root / 'out')

    def test_host_readback_is_bound_to_saved_source(self):
        generated = self.root / 'generated' / self.project.name
        generated.mkdir(parents=True)
        (generated / 'draft_content.json').write_bytes(self.original)
        sheet = self.root / 'sheet.json'
        atomic_json(sheet, {'clips': []})
        task = {'project': self.project.name, 'editor_project': str(self.project), 'spec': {
            'effective_sheet': {'path': str(sheet)},
            'project_artifact': {'path': str(generated / 'draft_content.json')},
            'expected': {'duration': 2, 'width': 1080, 'height': 1920, 'fps': 30., 'require_audio': True}}}
        self.file.write_bytes(b'A' * 64)
        real_snapshot = native.snapshot
        def isolated(folder):
            return real_snapshot(folder, self.root / 'out')
        with patch.object(native, 'snapshot', side_effect=isolated), \
                patch.object(native, 'pinned_codec', return_value=None), \
                patch.object(native, 'decode_file', side_effect=self.decoded_fixture), \
                patch('build_draft.runtime_asset_report', return_value={'status': 'complete'}):
            result = host._timeline(task)
        self.assertEqual(result['project_file']['path'], str(self.file))
        self.assertEqual(result['project_file']['sha256'], native.sha256(self.file))
        self.assertIn('readback', result)

    def test_missing_saved_fps_is_not_invented(self):
        self.data.pop('fps')
        atomic_json(self.file, self.data)
        generated = self.root / 'generated' / self.project.name
        generated.mkdir(parents=True)
        sheet = self.root / 'sheet.json'
        atomic_json(sheet, {})
        task = {'project': self.project.name, 'editor_project': str(self.project), 'spec': {
            'effective_sheet': {'path': str(sheet)}, 'expected': {'require_audio': True},
            'project_artifact': {'path': str(generated / 'draft_content.json')}}}
        with patch('build_draft.runtime_asset_report', return_value={'status': 'complete'}):
            with self.assertRaisesRegex(ValueError, 'does not persist fps'):
                host._timeline(task)

    def test_saved_native_missing_keyframe_times_report_review_instead_of_crashing(self):
        from aicut.execution_audit import audit_timeline
        data = {'tracks': [{'name': 'main', 'segments': [{
            'target_timerange': {'duration': 2000000}, 'source_timerange': {'duration': 2000000},
            'common_keyframes': [{'property_type': 'UNIFORM_SCALE', 'keyframe_list': [
                {'values': [1.]}, {'time_offset': 1000000, 'values': [1.08]}]}]}]}]}
        sheet = {'clips': [{'file': str(self.file), 'start': 0, 'end': 2}]}
        report = audit_timeline(sheet, data, lambda p: p)
        self.assertEqual(report['status'], 'needs_review')
        self.assertTrue(any(r['field'].endswith('.keyframes') and r['status'] != 'applied'
                            for r in report['checks']))

    def test_native_save_can_remove_catalog_bounds_but_not_change_controls(self):
        from aicut.execution_audit import _effect_controls_equal
        expected = [{'name': 'speed', 'value': .33, 'min_value': 0., 'max_value': 1.,
                     'parameterIndex': 0, 'portIndex': 0}]
        self.assertTrue(_effect_controls_equal(expected, [{'name': 'speed', 'value': .33}]))
        self.assertFalse(_effect_controls_equal(expected, [{'name': 'speed', 'value': 1.}]))
        self.assertFalse(_effect_controls_equal(expected, [{'name': 'wrong', 'value': .33}]))
        self.assertFalse(_effect_controls_equal(expected, [{'name': 'speed', 'value': .33, 'portIndex': 4}]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
