"""Image-grounded visual memory comparison tests with a mocked local VLM."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from aicut import visual_memory


class VisualMemoryTests(unittest.TestCase):
    def test_reference_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            frame = root / 'frame.jpg'
            Image.new('RGB', (32,32), 'blue').save(frame)
            sha = hashlib.sha256(frame.read_bytes()).hexdigest()
            row = {'id': 's', 'category': 'paired_visual_study',
                   'frames': [{'path': 'frame.jpg', 'sha256': sha}],
                   'frame_observations': [{'frame_sha256': sha, 'vision_analysis': {'subject': 'blue'}}]}
            self.assertEqual(len(visual_memory._reference_rows([row], root, 2)), 1)
            Image.new('RGB', (32,32), 'red').save(frame)
            self.assertEqual(visual_memory._reference_rows([row], root, 2), [])

    def test_explicit_local_vision_budget_and_truncation(self):
        from aicut import llm
        with patch.object(llm, '_b64', return_value='image'), \
             patch.object(llm, '_post', return_value={'message': {'content': '{"ok": true}'}, 'done_reason': 'stop'}) as post:
            self.assertTrue(llm.vision_json('unused.jpg', 'inspect', provider='ollama', model='test', max_tokens=2400)['ok'])
            self.assertEqual(post.call_args.args[1]['options']['num_predict'], 2400)
            post.return_value={'message': {'content': '{"ok": true}'}, 'done_reason': 'length'}
            with self.assertRaises(RuntimeError):
                llm.vision_json('unused.jpg', 'inspect', provider='ollama', model='test', max_tokens=2400)

    def test_compares_source_pixels_to_retrieved_study_and_caches_reasoning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.jpg"
            ref_path = root / "study.jpg"
            Image.new("RGB", (64, 48), "#d02020").save(source_path)
            Image.new("RGB", (64, 48), "#2040d0").save(ref_path)
            source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            profile = {"shot_id": "shot_1", "t0": 1.0, "t1": 2.0,
                       "content": "红色主体", "semantic_evidence": {
                           "status": "automatic_visual_observation_unreviewed",
                           "image": str(source_path), "timestamp": 1.5,
                           "sha256": source_hash}}
            fake_doc = {"id": "study-1", "category": "paired_visual_study",
                        "source": "test", "video_path": "study.mp4", "video_metadata": {},
                        "frame_observation_count": 1, "vision_model": "test-vlm",
                        "learning_dimensions": ["镜头覆盖"], "status": "paired",
                        "title": "蓝色参考视频", "source_url": "https://example.test/video",
                        "visual_category": "短视频平台风格代理", "video_sha256": "video-hash",
                        "platform_scope": "short_video_style_proxy", "review_status": "automatic_analysis_not_human_reviewed",
                        "frames": [], "frame_observations": []}
            bundle = {"query": "短视频 镜头 vlog", "evidence": [fake_doc]}
            result = {"当前画面观察": [{"panel": "CURRENT 1", "可见事实": "red subject"}],
                      "相似样本比较": [{"当前镜头": "CURRENT 1", "样本id": "study-1", "可迁移做法": "保留主体"}],
                      "剪辑思考": [{"镜头panel": "CURRENT 1", "建议": "开头先给主体特写", "理由": "主体可见"}]}
            reference_rows = [{"id": "study-1", "title": "蓝色参考视频",
                               "source_url": "https://example.test/video",
                               "platform_scope": "short_video_style_proxy",
                               "video_sha256": "video-hash",
                               "review_status": "automatic_analysis_not_human_reviewed",
                               "visual_category": "短视频平台风格代理", "style_type": "vlog",
                               "learning_dimensions": ["镜头覆盖"],
                               "frames": [{"path": ref_path, "timestamp": 3.0,
                                           "sha256": "ref-hash", "analysis": {"主体": "蓝色主体"}}]}]
            with patch.object(visual_memory, "_reference_rows", return_value=reference_rows), \
                 patch.object(visual_memory, "config", return_value={'vision': {'bundled': True}}), \
                 patch("aicut.bundled_vision.info", return_value={"id": "test-vlm"}), \
                 patch("aicut.bundled_vision.analyze_frame", return_value=result) as analyze:
                compared = visual_memory.compare_source_to_memory(
                    [profile], bundle, goal="制作短视频", root=root)
                self.assertEqual(compared["status"], "analyzed")
                self.assertTrue(compared["model_saw_images"])
                self.assertEqual(compared["reference_studies"][0]["id"], "study-1")
                board = Path(compared["board_path"])
                self.assertTrue(board.is_file())
                expected_width = 0
                for image_path in (source_path, ref_path):
                    with Image.open(image_path) as sample:
                        sample.thumbnail((visual_memory.CELL_W, visual_memory.IMAGE_H), Image.Resampling.LANCZOS)
                        expected_width += sample.width
                with Image.open(board) as opened:
                    self.assertEqual(opened.height, 330)
                    self.assertEqual(opened.width, expected_width)
                manifest = json.loads(Path(compared["manifest_path"]).read_text(encoding="utf-8"))
                self.assertEqual([p["panel"] for p in manifest["panels"]],
                                 ["CURRENT 1", "REFERENCE 1, row 1"])
                self.assertEqual(analyze.call_count, 2)
                self.assertEqual(analyze.call_args_list[0].args[0], board)
                self.assertEqual(analyze.call_args_list[1].args[0], source_path)
                cached = visual_memory.compare_source_to_memory(
                    [profile], bundle, goal="制作短视频", root=root)
                self.assertTrue(cached["cached"])
                self.assertEqual(analyze.call_count, 2)
                with patch.object(visual_memory, 'config', return_value={'vision': {'bundled': False}}), \
                     patch('aicut.llm.get_vision_model', return_value='configured-local-model'), \
                     patch('aicut.llm.vision_json', return_value=result) as local:
                    routed = visual_memory.compare_source_to_memory([profile], bundle, root=root)
                    self.assertEqual(routed['backend'], 'ollama')
                    self.assertEqual(local.call_args.kwargs['model'], 'configured-local-model')
                    self.assertEqual(local.call_args.kwargs['provider'], 'ollama')
                    self.assertEqual(analyze.call_count, 2)
                Image.new('RGB', (64,48), 'green').save(source_path)
                self.assertEqual(visual_memory._source_frames([profile], 4), [])

    def test_missing_real_source_image_is_explicitly_skipped(self):
        result = visual_memory.compare_source_to_memory([], {"evidence": []}, root=Path.cwd())
        self.assertEqual(result["status"], "skipped_no_source_images")
        self.assertFalse(result["model_saw_images"])

    def test_empty_or_unbound_visual_result_is_rejected(self):
        for result in ({}, {'当前画面观察': [{'panel': 'CURRENT 1', '可见事实': 'person'}],
                           '相似样本比较': [{'当前镜头': 'CURRENT 1', '样本id': 'invented', '可迁移做法': 'zoom'}]}):
            with self.assertRaises(ValueError):
                visual_memory.validate_analysis(result, {'study-1'}, {'CURRENT 1'})
        mixed={'当前画面观察': [{'panel': 'CURRENT 1', '可见事实': 'person'}],
               '相似样本比较': [{'当前镜头': 'CURRENT 1', '样本id': 'study-1', '可迁移做法': 'hold'},
                             {'当前镜头': 'CURRENT 1', '样本id': 'invented', '可迁移做法': 'zoom'}],
               '剪辑思考': [{'镜头panel': 'CURRENT 1', '建议': 'hold', '理由': 'visible'}]}
        with self.assertRaises(ValueError):
            visual_memory.validate_analysis(mixed, {'study-1'}, {'CURRENT 1'})


if __name__ == "__main__":
    unittest.main(verbosity=2)
