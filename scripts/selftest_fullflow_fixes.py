"""Regressions for failures found by the real-media full-flow acceptance run."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'lib'), str(ROOT/'scripts')]
import preprocess
import understand
from aicut import llm, semantic_profile, paths, framewise


class FullflowFixTests(unittest.TestCase):
    def test_smart_understanding_prefers_configured_vision(self):
        with patch.object(llm,'get_vision_model',return_value='configured-vision'), \
             patch.object(llm,'assert_local_vision_healthy') as health, \
             patch.object(understand,'model_supports_vision',return_value=True), \
             patch.object(understand,'local_vision_models',return_value=['legacy-small']):
            self.assertEqual(understand.select_vision_model(None),'configured-vision')
        health.assert_called_once_with('configured-vision')

    def test_smart_understanding_skips_quarantined_checkpoint(self):
        def health(model):
            if model == 'failed-small':
                raise RuntimeError('quarantined')
        with patch.object(llm,'get_vision_model',return_value='failed-small'), \
             patch.object(llm,'assert_local_vision_healthy',side_effect=health), \
             patch.object(understand,'model_supports_vision',return_value=True), \
             patch.object(understand,'local_vision_models',return_value=['healthy-model']):
            self.assertEqual(understand.select_vision_model(None),'healthy-model')
            with self.assertRaises(RuntimeError):
                understand.select_vision_model('failed-small')

    def test_frame_schema_reaches_ollama_format(self):
        observed = {'subject':'person','action':'holding doll','objects':['doll'],'visible_text':''}
        with patch.object(llm,'assert_local_vision_healthy'), \
             patch.object(llm,'_b64',return_value='image-data'), \
             patch.object(llm,'_post',return_value={'message':{'content':json.dumps(observed)}}) as post:
            out = llm.vision_json('image.jpg',framewise.PROMPT,provider='ollama',model='test',
                                  schema=framewise.OBSERVATION_SCHEMA)
        payload = post.call_args.args[1]
        self.assertEqual(payload['format'],framewise.OBSERVATION_SCHEMA)
        self.assertIn('additionalProperties',payload['messages'][0]['content'])
        self.assertTrue(framewise.valid_observation(out))

    def test_normalization_writes_explicit_square_pixels_from_unknown_sar(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'unknown-sar.mp4'
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
                'testsrc2=s=180x320:r=24:d=0.5','-vf','setsar=0',
                '-c:v','libx264','-pix_fmt','yuv420p',str(source)], check=True)
            cfg = {'ffmpeg':'ffmpeg','ffprobe':'ffprobe', 'render': {
                'width':180,'height':320,'fps':30,'preset':'ultrafast',
                'crf':24,'audio_bitrate':'128k'}}
            result = preprocess.normalize(source, 'normalized', cfg, root)
            self.assertEqual(preprocess.probe(Path(result['video']),'ffprobe')['sample_aspect_ratio'], '1:1')

    def test_semantic_uses_selected_vision_model(self):
        with patch.object(llm,'get_vision_model',return_value='configured-vision'), \
             patch.object(llm,'assert_local_vision_healthy') as health, \
             patch.object(llm,'_b64',return_value='image-data'), \
             patch.object(llm,'_post',return_value={'message':{'content':'{"主角":"visible person"}'}}) as post, \
             patch.object(Path,'exists',return_value=True):
            out = semantic_profile._vision_json_multi([Path('frame.jpg')],'describe',use_cloud=False)
        health.assert_called_once_with('configured-vision')
        self.assertEqual(post.call_args.args[1]['model'], 'configured-vision')
        self.assertEqual(out['_model'], 'configured-vision')

    def test_semantic_does_not_bypass_quarantined_checkpoint(self):
        with patch.object(llm,'get_vision_model',return_value='failed-model'), \
             patch.object(llm,'assert_local_vision_healthy',side_effect=RuntimeError('quarantined')), \
             patch.object(llm,'_b64',return_value='image-data'), \
             patch.object(llm,'_post') as post, patch.object(Path,'exists',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'quarantined'):
                semantic_profile._vision_json_multi([Path('frame.jpg')],'describe',use_cloud=False)
        post.assert_not_called()

    def test_semantic_fallback_keeps_selected_model(self):
        with patch.object(llm,'get_vision_model',return_value='configured-vision'), \
             patch.object(llm,'assert_local_vision_healthy'), \
             patch.object(llm,'_b64',return_value='image-data'), \
             patch.object(llm,'_post',side_effect=RuntimeError('multi-image failed')), \
             patch.object(llm,'vision_json',return_value={'主角':'person'}) as fallback, \
             patch.object(Path,'exists',return_value=True):
            semantic_profile._vision_json_multi([Path('frame.jpg')],'describe',use_cloud=False)
        self.assertEqual(fallback.call_args.kwargs['model'],'configured-vision')
        self.assertEqual(fallback.call_args.kwargs['provider'],'ollama')

    def test_health_guard_binds_failure_to_checkpoint_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            identity = hashlib.sha256(b'checkpoint').hexdigest()
            (root/'vision-model-health.json').write_text(json.dumps({
                'bad':{'status':'failed','identity':identity,'reason':'real-frame mismatch'}}),encoding='utf-8')
            with patch.object(paths,'WORK',root), patch.dict(os.environ,{'AICUT_VISION_DIAGNOSTIC':'0'}), \
                 patch.object(llm,'_post',return_value={'modelfile':'checkpoint'}):
                with self.assertRaisesRegex(RuntimeError,'real-frame mismatch'):
                    llm.assert_local_vision_healthy('bad')
            with patch.object(paths,'WORK',root), patch.object(llm,'_post',return_value={'modelfile':'new-checkpoint'}):
                llm.assert_local_vision_healthy('bad')


if __name__ == '__main__':
    unittest.main(verbosity=2)
