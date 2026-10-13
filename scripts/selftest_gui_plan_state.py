import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'lib')]
from PySide6.QtCore import QCoreApplication
from gui.workers import PipelineWorker
from aicut import llm, llm_router


class GuiPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_planned_is_not_reported_as_generated_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = SimpleNamespace(project_root=root, scripts_dir=root / 'scripts', get=lambda key, default=None: default)
            worker = PipelineWorker(cfg, 'edit', {'video': 'fixture.mp4'})
            status = root / 'work' / 'tasks' / worker.task_id / 'status.json'
            status.parent.mkdir(parents=True)
            status.write_text(json.dumps({'state': 'planned'}))
            results = []
            worker.finished.connect(lambda ok, result: results.append((ok, result)))
            with patch.object(worker, '_run', return_value=0):
                worker._do_edit()
            self.assertEqual(results, [(True, 'PLAN:' + str(status))])
            status.write_text(json.dumps({'state': 'needs_review'}))
            with patch.object(worker, '_run', return_value=0):
                worker._do_edit()
            self.assertFalse(results[-1][0])

    def test_shared_model_warmup_does_not_load_old_text_model(self):
        with patch.object(llm, 'get_vision_model', return_value='verified'), \
             patch.object(llm_router, 'route_backends', return_value=[SimpleNamespace(type='ollama', model='verified')]), \
             patch.object(llm, '_post', return_value={}) as post:
            self.assertEqual(llm.warmup_models()['text'], 'shared_with_vision')
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.args[1]['options']['num_ctx'], llm.OLLAMA_NUM_CTX)

    def test_approved_delivery_passes_editor_and_preserves_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = SimpleNamespace(project_root=root, scripts_dir=root / 'scripts')
            worker = PipelineWorker(cfg, 'build_approved', {'sheet': 'approved.json', 'sha256': 'confirmed',
                                                          'software': 'premiere', 'engine': 'portable', 'publish': True})
            with patch.object(worker, '_run', return_value=0) as run:
                worker._do_build_approved()
            args = run.call_args.args[0]
            self.assertIn('deliver_edit.py', args[0])
            self.assertEqual(args[args.index('--software') + 1], 'premiere')
            self.assertEqual(args[args.index('--engine') + 1], 'portable')
            self.assertEqual(args[args.index('--approve-plan-sha256') + 1], 'confirmed')
            self.assertNotIn('--publish', args)

    def test_delivery_result_loads_video_and_keeps_visual_review_pending(self):
        from gui.mainwindow import MainWindow
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'delivery.json'
            report.write_text(json.dumps({'status': 'rendered_pending_visual_review',
                                          'artifacts': {'video': 'verified.mp4'}}))
            window = SimpleNamespace(_set_busy=MagicMock(), sb_progress=MagicMock(), sb_stage=MagicMock(),
                                     sb_pipe=MagicMock(), preview=MagicMock(), bottom=MagicMock())
            MainWindow._on_finished(window, True, 'DELIVERY:' + str(report))
            window.preview.load_video.assert_called_once_with('verified.mp4')
            self.assertIn('待画面复核', window.sb_stage.setText.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
