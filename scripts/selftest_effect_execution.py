"""Isolated native effect-control compilation and resource roundtrip checks."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'scripts')]
from JianYingDraft import ScriptFile, TrackType, trange
from aicut.effect_execution import compile_effect, compile_filter, native_picture_config
from aicut.editor_capabilities import preflight
from aicut.execution_audit import audit_timeline
import build_draft

SPEED = 'effects_adjust_speed'
BACKGROUND = 'effects_adjust_background_animation'


def decoration_material(draft, kind):
    segment = next(t for t in draft['tracks'] if t['name'] == kind)['segments'][0]
    return next(m for group in draft['materials'].values() for m in group
                if m.get('id') == segment['material_id'])


class EffectExecutionTests(unittest.TestCase):
    def sheet(self):
        return {'project': 'isolated-effect-controls', 'timeline_prepared': True,
                '画面': {'滤镜': '清晰明亮', '滤镜强度': 15},
                'clips': [{'file': 'fixture.mp4', 'start': 0, 'end': 1,
                           '特效': '光晕', 'effect_params': {BACKGROUND: 20, SPEED: 10}}]}

    def draft(self, sheet=None):
        sheet = sheet or self.sheet()
        script = ScriptFile(360, 640)
        script.add_track(TrackType.effect, 'effect')
        script.add_track(TrackType.filter, 'filter')
        cursor = 0
        main = []
        for i, clip in enumerate(sheet['clips']):
            source_us = round((clip['end'] - clip['start']) * 1e6)
            duration_us = round(source_us / clip.get('speed', 1))
            timerange = {'start': cursor, 'duration': duration_us}
            effect = compile_effect(clip)
            if effect:
                script.add_effect(effect.member, trange(cursor, duration_us), 'effect', params=effect.params)
            filt = compile_filter(clip, sheet['画面'])
            script.add_filter(filt.member, trange(cursor, duration_us), 'filter', intensity=filt.intensity)
            main.append({'material_id': f'video{i}', 'target_timerange': timerange,
                         'source_timerange': {'start': round(clip['start'] * 1e6), 'duration': source_us},
                         'speed': clip.get('speed', 1), 'volume': clip.get('volume', 1), 'common_keyframes': []})
            cursor += duration_us
        draft = json.loads(script.dumps())
        draft['materials']['videos'] = [{'id': f'video{i}', 'path': clip['file']}
                                        for i, clip in enumerate(sheet['clips'])]
        draft['tracks'].insert(0, {'name': 'main', 'type': 'video', 'segments': main})
        return draft

    def test_named_controls_use_native_metadata_order_and_ranges(self):
        compiled = compile_effect(self.sheet()['clips'][0])
        self.assertEqual(compiled.params, [10, 20])
        self.assertEqual([(p['name'], p['value']) for p in compiled.adjust_params], [(SPEED, .1), (BACKGROUND, .2)])
        partial = compile_effect({'特效': '光晕', '特效参数': {BACKGROUND: 0}})
        self.assertEqual(partial.params, [None, 0])
        self.assertEqual([p['value'] for p in partial.adjust_params], [.33, 0])

    def test_absent_parameters_preserve_native_defaults(self):
        compiled = compile_effect({'effect': '光晕'})
        self.assertIsNone(compiled.params)
        self.assertEqual([p['value'] for p in compiled.adjust_params], [.33, 1])
        draft = self.draft()
        material = draft['materials']['video_effects'][0]
        self.assertEqual([p['value'] for p in material['adjust_params']], [.1, .2])

    def test_invalid_parameter_names_types_ranges_and_orphans_fail(self):
        for value in (float('nan'), float('inf'), -1, 101, True, '20', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compile_effect({'特效': '光晕', 'effect_params': {SPEED: value}})
        for controls in ({'intensity': 20}, {'强度': 20}, [10, 20], None):
            with self.subTest(controls=controls), self.assertRaises(ValueError):
                compile_effect({'特效': '光晕', 'effect_params': controls})
        with self.assertRaisesRegex(ValueError, 'requires an effect'):
            compile_effect({'effect_params': {}})

    def test_conflicting_aliases_are_rejected(self):
        for clip in ({'特效': '光晕', 'effect': 'unknown'},
                     {'特效': '光晕', '特效参数': {SPEED: 10}, 'effect_params': {SPEED: 20}}):
            with self.assertRaisesRegex(ValueError, 'conflicting aliases'):
                compile_effect(clip)
        with self.assertRaisesRegex(ValueError, 'conflicting aliases'):
            compile_filter({'滤镜': '清晰明亮', '滤镜强度': 10, 'filter_intensity': 20})

    def test_native_preflight_rejects_unknown_resources_and_bad_controls(self):
        for patch in ({'特效': 'not-a-resource'}, {'滤镜': 'not-a-resource'},
                      {'effect_params': {SPEED: float('nan')}}, {'滤镜强度': 101}):
            sheet = self.sheet()
            sheet['clips'][0].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                preflight(sheet, 'jianying', 'project')
        preflight(self.sheet(), 'capcut', 'project')

    def test_portable_explicit_native_controls_are_blocked(self):
        sheet = self.sheet()
        sheet.pop('画面')
        with self.assertRaisesRegex(ValueError, 'cannot reproduce native effect_params'):
            preflight(sheet, 'premiere', 'portable')
        sheet['clips'][0].pop('effect_params')
        preflight(sheet, 'premiere', 'portable')  # Existing unparameterized approximation stays compatible.

    def test_filter_intensity_validation(self):
        for value in (float('nan'), float('inf'), -1, 101, True, '15'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compile_filter({'filter': '清晰明亮', 'filter_intensity': value})
        with self.assertRaisesRegex(ValueError, 'requires a filter'):
            compile_filter({'filter_intensity': 10})
        self.assertEqual(compile_filter({'filter': '清晰明亮', 'filter_intensity': 0}).intensity, 0)

    def test_picture_aliases_override_preset_without_false_conflicts(self):
        picture = native_picture_config({'画面': {'filter': '清晰明亮', 'filter_intensity': 8}})
        self.assertEqual(compile_filter({}, picture).intensity, 8)
        with self.assertRaisesRegex(ValueError, 'conflicting aliases'):
            native_picture_config({'画面': {'滤镜强度': 8, 'filter_intensity': 9}})

    def test_roundtrip_resource_timerange_controls_and_filter_value(self):
        sheet = self.sheet()
        draft = self.draft(sheet)
        self.assertEqual(audit_timeline(sheet, draft, build_draft.resolve)['status'], 'complete')
        self.assertEqual(decoration_material(draft, 'filter')['value'], .15)
        mutations = [
            ('effect resource', lambda d: d['materials']['video_effects'][0].update(resource_id='wrong')),
            ('effect id', lambda d: d['materials']['video_effects'][0].update(effect_id='wrong')),
            ('effect control', lambda d: d['materials']['video_effects'][0]['adjust_params'][1].update(value=1)),
            ('effect control name', lambda d: d['materials']['video_effects'][0]['adjust_params'][0].update(name='intensity')),
            ('filter resource', lambda d: decoration_material(d, 'filter').update(resource_id='wrong')),
            ('filter intensity', lambda d: decoration_material(d, 'filter').update(value=1)),
        ]
        for label, change in mutations:
            corrupted = copy.deepcopy(draft)
            change(corrupted)
            with self.subTest(label=label):
                self.assertEqual(audit_timeline(sheet, corrupted, build_draft.resolve)['status'], 'needs_review')
        for kind in ('effect', 'filter'):
            for field in ('start', 'duration'):
                corrupted = copy.deepcopy(draft)
                track = next(t for t in corrupted['tracks'] if t['name'] == kind)
                track['segments'][0]['target_timerange'][field] += 100000
                with self.subTest(kind=kind, field=field):
                    self.assertEqual(audit_timeline(sheet, corrupted, build_draft.resolve)['status'], 'needs_review')
            corrupted = copy.deepcopy(draft)
            track = next(t for t in corrupted['tracks'] if t['name'] == kind)
            track['segments'][0]['material_id'] = 'missing-material'
            self.assertEqual(audit_timeline(sheet, corrupted, build_draft.resolve)['status'], 'needs_review')

    def test_effects_follow_edited_clip_time_including_unadorned_clips(self):
        sheet = self.sheet()
        sheet['clips'].insert(0, {'file': 'fixture.mp4', 'start': 1, 'end': 3, 'speed': 2})
        draft = self.draft(sheet)
        self.assertEqual(audit_timeline(sheet, draft, build_draft.resolve)['status'], 'complete')
        effect = next(t for t in draft['tracks'] if t['name'] == 'effect')['segments'][0]
        self.assertEqual(effect['target_timerange'], {'start': 1000000, 'duration': 1000000})

    def test_runtime_asset_report_fails_despite_unchanged_segment_counts(self):
        sheet = self.sheet()
        draft = self.draft(sheet)
        draft['materials']['video_effects'][0]['adjust_params'][1]['value'] = .99
        with tempfile.TemporaryDirectory(prefix='aicut-effect-audit-') as folder:
            path = Path(folder)
            (path / 'draft_content.json').write_text(json.dumps(draft), encoding='utf-8')
            report = build_draft.runtime_asset_report(sheet, path)
            self.assertEqual(report['checks']['video_effects']['status'], 'applied')
            self.assertEqual(report['status'], 'needs_review')
            self.assertFalse(report['timeline']['host_render_verified'])

    def test_build_serializes_explicit_controls_and_global_filter(self):
        with tempfile.TemporaryDirectory(prefix='aicut-effect-build-') as folder:
            root = Path(folder)
            source = root / 'source.mp4'
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=s=64x96:r=30:d=2', '-c:v', 'libx264', '-preset', 'ultrafast',
                            '-pix_fmt', 'yuv420p', str(source)], check=True, timeout=45)
            sheet = self.sheet()
            sheet.update(width=360, height=640, fps=30)
            sheet['clips'][0].update(file=str(source), end=2, speed=2)
            path = build_draft.build(sheet, build_draft.load_config(), output_root=root / 'drafts')
            draft = json.loads((path / 'draft_content.json').read_text(encoding='utf-8'))
            self.assertEqual([p['value'] for p in draft['materials']['video_effects'][0]['adjust_params']], [.1, .2])
            self.assertEqual(decoration_material(draft, 'filter')['value'], .15)
            report = json.loads((path / 'aicut-runtime-assets.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'], 'complete')


if __name__ == '__main__':
    unittest.main(verbosity=2)
