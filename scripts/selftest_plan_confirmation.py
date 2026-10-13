import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import build_draft


class ConfirmationTests(unittest.TestCase):
    def test_confirmed_source_conflict_cannot_be_forced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sheet.json'
            path.write_text(json.dumps({'requires_plan_confirmation': True,
                'source_review': {'blocking': True}}), encoding='utf-8')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertIn('剪辑方案与原始素材存在未修复冲突',
                          build_draft.quality_gates(json.loads(path.read_text(encoding='utf-8'))))
            with patch.object(build_draft, 'load_config', return_value={}), \
                 patch.object(build_draft, 'quality_gates',
                              return_value=['剪辑方案与原始素材存在未修复冲突']), \
                 patch.object(build_draft, 'build') as build, \
                 patch.object(sys, 'argv', ['build_draft', str(path), '--force',
                     '--approve-plan-sha256', digest]):
                self.assertEqual(build_draft.main(), 2)
                build.assert_not_called()

    def test_confirmation_is_bound_to_exact_sheet(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sheet.json'
            path.write_text(json.dumps({'requires_plan_confirmation': True}), encoding='utf-8')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(build_draft, 'load_config', return_value={}), \
                 patch.object(build_draft, 'quality_gates', return_value=[]), \
                 patch.object(build_draft, 'build', side_effect=RuntimeError('test-only build sentinel')) as build:
                with patch.object(sys, 'argv', ['build_draft', str(path), '--force']):
                    self.assertEqual(build_draft.main(), 4)
                build.assert_not_called()
                with patch.object(sys, 'argv', ['build_draft', str(path), '--approve-plan-sha256', digest]):
                    self.assertEqual(build_draft.main(), 3)
                self.assertEqual(build.call_count, 1)
                path.write_text(json.dumps({'requires_plan_confirmation': True, 'revision': 2}), encoding='utf-8')
                with patch.object(sys, 'argv', ['build_draft', str(path), '--approve-plan-sha256', digest]):
                    self.assertEqual(build_draft.main(), 4)
                self.assertEqual(build.call_count, 1)


if __name__ == '__main__':
    unittest.main()
