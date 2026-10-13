"""Integrity failures and coverage corruption must stop the quality pipeline."""
import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aicut.media_integrity import audit_decode
from aicut import media_integrity
from aicut.shotprof import quality_coverage
from aicut.vision import extract_frames


class IntegrityTests(unittest.TestCase):
    def test_source_mapping_requires_manifest_and_existing_original(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(media_integrity, 'WORK', Path(folder)):
            root = Path(folder)
            normalized, original = root / 'normalized.mp4', root / 'original.mov'
            normalized.write_bytes(b'proxy')
            original.write_bytes(b'original')
            (root / 'manifest.json').write_text(json.dumps({'items': [
                {'video': str(normalized), 'source': str(original)}]}))
            self.assertEqual(media_integrity.resolve_original(normalized), original)
            original.unlink()
            with self.assertRaises(FileNotFoundError):
                media_integrity.resolve_original(normalized)

    def test_cyclic_source_mapping_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(media_integrity, 'WORK', Path(folder)):
            root = Path(folder)
            a, b = root / 'a.mp4', root / 'b.mp4'
            a.touch(); b.touch()
            (root / 'manifest.json').write_text(json.dumps({'items': [
                {'video': str(a), 'source': str(b)}, {'video': str(b), 'source': str(a)}]}))
            with self.assertRaises(ValueError):
                media_integrity.resolve_original(a)

    def test_decoder_nonzero_and_missing_eof_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.mp4'
            path.write_bytes(b'fixture')
            for code, progress in [(1, 'frame=30\nprogress=end'), (0, 'frame=30\nprogress=continue'),
                                   (0, 'frame=0\nprogress=end')]:
                with self.subTest(code=code, progress=progress), patch(
                    'aicut.media_integrity.subprocess.run', return_value=SimpleNamespace(
                        returncode=code, stdout=progress, stderr='decode diagnostic')):
                    self.assertFalse(audit_decode(path)['complete'])

    def test_extraction_failure_does_not_return_partial_images(self):
        with tempfile.TemporaryDirectory() as folder, patch(
            'aicut.vision.subprocess.run', return_value=SimpleNamespace(returncode=1, stderr='corrupt')):
            with self.assertRaises(RuntimeError):
                extract_frames('input.mp4', Path(folder))

    def test_duplicate_times_and_shot_gaps_rejected(self):
        stream = {'fps': 2, 'frames': [{'t': i / 2} for i in range(8)]}
        result = {'duration': 4, 'shots': [{'t0': 0, 't1': 4, '_analysis_status': 'analyzed'}]}
        self.assertTrue(quality_coverage(result, stream)['complete'])
        self.assertFalse(quality_coverage(result, stream)['semantic_verified'])
        stream['frames'].insert(3, {'t': 1})
        self.assertFalse(quality_coverage(result, stream)['complete'])
        stream['frames'].pop(3)
        result['shots'][0]['t0'] = 1
        self.assertFalse(quality_coverage(result, stream)['complete'])


if __name__ == '__main__':
    unittest.main()
