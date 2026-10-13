"""Regression + synthetic real-media delivery tests. No model/API or host UI."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'scripts')]
from aicut.sheet2ir import sheet_to_project
from aicut.editor_capabilities import preflight
from aicut.execution_audit import audit_timeline
from editor_bridges.jianying import JianYingBridge
import build_draft
import deliver_edit


class DeliveryTests(unittest.TestCase):
    def sheet(self):
        return {'project': 'delivery-regression', 'clips': [
            {'file': 'source.mp4', 'start': 0, 'end': 10, 'speed': 2, 'volume': 0,
             'keyframes': [{'property': 'scale', 't': 0, 'value': 1}, {'property': 'scale', 't': 5, 'value': 1.1}]},
            {'file': 'source.mp4', 'start': 10, 'end': 12, 'speed': .5}],
            'bgm': {'file': 'music.wav', 'volume': 0}}

    def test_speed_and_cursor(self):
        p = sheet_to_project(self.sheet(), ROOT)
        self.assertEqual(p.duration, 9)
        self.assertEqual(p.tracks[0].segments[1].t, 5)
        self.assertEqual(p.tracks[0].segments[0].src_len, 10)
        self.assertEqual(p.tracks[0].segments[0].volume, 0)
        self.assertEqual(p.tracks[1].segments[0].volume, 0)

    def test_deep_preservation(self):
        s = self.sheet(); s['clips'][0]['转场调用名'] = '渐隐渐显'
        p = sheet_to_project(s, ROOT)
        s['clips'][0]['keyframes'][0]['value'] = 99
        self.assertEqual(p.tracks[0].segments[0].extra['keyframes'][0]['value'], 1)
        self.assertEqual(p.tracks[0].segments[0].extra['转场调用名'], '渐隐渐显')

    def test_invalid_speed_rejected(self):
        for bad in (0, -1, float('nan'), float('inf')):
            s = self.sheet(); s['clips'][0]['speed'] = bad
            with self.assertRaises(ValueError):
                sheet_to_project(s, ROOT)

    def test_basic_writer_volume_roundtrip(self):
        s = self.sheet(); s['clips'][0].pop('keyframes')
        with tempfile.TemporaryDirectory() as folder:
            path = JianYingBridge().write(sheet_to_project(s, ROOT), folder)
            p = JianYingBridge().read(path)
            self.assertEqual(p.tracks[0].segments[0].volume, 0)
            self.assertEqual(p.tracks[1].segments[0].volume, 0)
            self.assertEqual(p.tracks[0].segments[0].length, 5)

    def test_unsupported_writer_fails_before_json(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'cannot serialize'):
                JianYingBridge().write(sheet_to_project(self.sheet(), ROOT), folder)
            self.assertFalse((Path(folder) / 'draft_content.json').exists())

    def test_placeholder_adapter_and_native_export_blocked(self):
        for software in ('premiere', 'davinci', 'finalcut', 'vegas', 'aftereffects'):
            with self.assertRaises(ValueError): preflight(self.sheet(), software, 'project')
        with self.assertRaises(ValueError): preflight(self.sheet(), 'jianying', 'native')

    def test_native_text_speed_references_registered(self):
        from JianYingDraft import ScriptFile, TextSegment, TrackType, trange
        script = ScriptFile(1080, 1920)
        script.add_track(TrackType.text, 'subtitle')
        text = TextSegment('字幕测试', trange(0, 1_000_000))
        script.add_segment(text, 'subtitle')
        ids = {m['id'] for group in script.materials.export_json().values() for m in group if m.get('id')}
        self.assertTrue(set(text.export_json()['extra_material_refs']).issubset(ids))

    def test_confirmation_rejects_changed_sheet_before_build(self):
        s = self.sheet(); s['requires_plan_confirmation'] = True
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sheet.json'; path.write_text(json.dumps(s))
            with patch.object(build_draft, 'build') as build:
                _, report = deliver_edit.deliver(path, approved_sha256='outdated')
            self.assertEqual(report['status'], 'blocked')
            build.assert_not_called()

    def test_invalid_motion_and_portable_effect_blocked(self):
        for kf in ({'property': 'bad', 't': 0, 'value': 1}, {'property': 'scale', 't': 6, 'value': 1}):
            s = self.sheet(); s['clips'][0]['keyframes'] = [kf]
            with self.assertRaises(ValueError): preflight(s, 'capcut', 'project')
        s = self.sheet(); s['clips'][0]['特效'] = 'unsupported'
        with self.assertRaises(ValueError): preflight(s, 'premiere', 'portable')

    def test_real_media_native_and_portable(self):
        with tempfile.TemporaryDirectory(prefix='aicut_delivery_') as folder:
            root = Path(folder); source = root / 'source.mp4'
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=360x640:r=30:d=4',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', '-shortest',
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True, timeout=60)
            s = {'project': 'delivery-regression', 'width': 360, 'height': 640, 'fps': 30, 'timeline_prepared': True,
                 'clips': [{'file': str(source), 'start': 0, 'end': 4, 'speed': 2, 'volume': .25,
                            'keyframes': [{'property': 'scale', 't': 0, 'value': 1}, {'property': 'scale', 't': 2, 'value': 1.12}]}]}
            draft = build_draft.build(s, build_draft.load_config(), output_root=root / 'native')
            data = json.loads((draft / 'draft_content.json').read_text(encoding='utf-8'))
            report = audit_timeline(s, data, build_draft.resolve)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(data['duration'], 2_000_000)
            tampered = copy.deepcopy(data)
            main = next(t for t in tampered['tracks'] if t['name'] == 'main')
            main['segments'][0]['common_keyframes'] = []
            self.assertEqual(audit_timeline(s, tampered, build_draft.resolve)['status'], 'needs_review')
            path = root / 'sheet.json'; path.write_text(json.dumps(s))
            output = root / 'rendered.mp4'
            _, delivery = deliver_edit.deliver(path, 'premiere', 'portable', output)
            self.assertEqual(delivery['status'], 'rendered_pending_visual_review', delivery)
            self.assertTrue(delivery['media_validation']['full_decode_passed'])
            self.assertFalse(delivery['executed_in_editor'])
            self.assertTrue(output.is_file())
            import cv2
            import numpy as np
            import export_video
            plain = copy.deepcopy(s); plain['clips'][0].pop('keyframes')
            baseline = root / 'baseline.mp4'
            subprocess.run(export_video.build_command(plain, baseline, True), check=True, capture_output=True, timeout=60)
            def frame(path):
                cap = cv2.VideoCapture(str(path)); cap.set(cv2.CAP_PROP_POS_MSEC, 1000)
                ok, image = cap.read(); cap.release()
                self.assertTrue(ok)
                return image.astype(float)
            difference = float(np.abs(frame(output) - frame(baseline)).mean())
            self.assertGreater(difference, 1, 'keyframes did not alter rendered pixels')


if __name__ == '__main__':
    unittest.main(verbosity=2)
