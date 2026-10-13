import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aicut import framewise
from aicut import edit_intent


class FramewiseTests(unittest.TestCase):
    def test_edit_prompt_accepts_compact_unreviewed_frame_timeline(self):
        prompt = edit_intent._build_prompt(
            [{'start':0, 'end':1, 'text':'开场'}], None, None,
            {'layer':'shortform','arch_id':'-','arch_name':'test','segments':[],
             'hook':'hook','cta':'cta','density':'normal'}, '',
            framewise_summary=[{'start':0.0,'end':0.04,'subject':'person','action':'talking',
                                'objects':[],'visible_text':'','frame_count':2,
                                'provenance':'automatic_visual_observation_unreviewed'}])
        self.assertIn('完整扫描后压缩', prompt)
        self.assertIn('未人工复核', prompt)
        self.assertIn('person', prompt)

    def test_resume_and_diagnostic_never_claims_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / 'test.mp4'
            video.write_bytes(b'media fixture')
            def subprocess_run(command, **kwargs):
                if '-version' in command:
                    return SimpleNamespace(stdout='ffmpeg test-build\n')
                if '-show_frames' in command:
                    return SimpleNamespace(stdout=json.dumps({'frames': [
                        {'best_effort_timestamp_time': '0.0'}, {'best_effort_timestamp_time': '0.04'}]}))
                pattern = Path(command[-1])
                for i in range(1, 3):
                    (pattern.parent / f'{i:08d}.jpg').write_bytes(bytes([i]))
                return SimpleNamespace(returncode=0)
            with patch.object(framewise, 'WORK', root), \
                 patch.object(framewise, 'audit_decode', return_value={'complete': True, 'decoded_video_frames': 2}), \
                 patch.object(framewise.subprocess, 'run', side_effect=subprocess_run), \
                 patch.object(framewise.llm, 'get_vision_model', return_value='fixture'), \
                 patch.object(framewise.llm, '_post', return_value={'modelfile': 'version1'}), \
                 patch.object(framewise.llm, 'vision_json', return_value={
                     'subject': 'person', 'action': 'sitting', 'objects': [], 'visible_text': ''}) as model:
                first = framewise.scan(video, limit=1)
                self.assertFalse(first['complete'])
                self.assertEqual(first['observed_frames'], 1)
                self.assertEqual(first['state'], 'diagnostic_incomplete')
                with self.assertRaises(ValueError):
                    framewise.summarize_for_edit(root / 'framewise' / first['identity'] / 'status.json')
                model.side_effect = TimeoutError('fixture timeout')
                with self.assertRaises(TimeoutError):
                    framewise.scan(video)
                failed = json.loads((root / 'framewise' / first['identity'] / 'status.json').read_text(encoding='utf-8'))
                self.assertEqual(failed['state'], 'failed')
                self.assertEqual(failed['observed_frames'], 1)
                self.assertEqual(failed['error']['frame_index'], 1)
                attempts = root / 'framewise' / first['identity'] / 'observations' / '00000001.attempts.json'
                self.assertEqual(len(json.loads(attempts.read_text(encoding='utf-8'))['attempts']), 3)
                model.side_effect = [{'subject': 'incomplete'},
                    {'subject': 'person', 'action': 'sitting', 'objects': [], 'visible_text': ''}]
                second = framewise.scan(video)
                model.side_effect = None
                self.assertTrue(second['complete'])
                self.assertEqual(model.call_count, 6)
                self.assertEqual(len(json.loads(attempts.read_text(encoding='utf-8'))['attempts']), 1)
                self.assertFalse(second['semantic_verified'])
                timeline = framewise.summarize_for_edit(root / 'framewise' / second['identity'] / 'status.json')
                self.assertEqual(len(timeline), 1)
                self.assertEqual(timeline[0]['start'], 0.0)
                self.assertEqual(timeline[0]['frame_count'], 2)
                self.assertTrue(timeline[0]['provenance'].endswith('_unreviewed'))
                # A stale extraction marker with a missing JPEG must rebuild
                # the full derived image set before resuming inference.
                image = root / 'framewise' / second['identity'] / 'images' / '00000002.jpg'
                image.unlink()
                third = framewise.scan(video)
                self.assertTrue(third['complete'])
                self.assertTrue(image.is_file())
                self.assertEqual(model.call_count, 6)
                video.write_bytes(b'updated source')
                fourth = framewise.scan(video, limit=1)
                self.assertNotEqual(third['identity'], fourth['identity'])
                self.assertFalse(fourth['complete'])
                self.assertEqual(model.call_count, 7)


if __name__ == '__main__':
    unittest.main()
