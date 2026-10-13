import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aicut import ollama_transport as transport
from aicut import llm_router


class StreamingTests(unittest.TestCase):
    def test_image_bytes_are_not_counted_as_prompt_text(self):
        backend = llm_router._build_backend({'name': 'fixture', 'type': 'ollama',
            'base_url': 'http://localhost:11434', 'model': 'fixture', 'vision_model': 'fixture'})
        with patch.object(llm_router, '_post_json', return_value={
            'message': {'content': '{}'}, 'done_reason': 'stop'}) as post:
            llm_router._call_ollama(backend, [{'role': 'user', 'content': 'Describe image'}],
                images_b64=['A' * 400000], json_mode=True, temperature=0, timeout=10,
                max_output_tokens=512)
            self.assertLessEqual(post.call_args.args[1]['options']['num_ctx'], 32768)
            self.assertEqual(len(post.call_args.args[1]['messages'][0]['images'][0]), 400000)

    def test_same_endpoint_model_is_not_retried_under_another_name(self):
        config = {'type': 'ollama', 'base_url': 'http://localhost:11434', 'model': 'fixture',
                  'prefer': ['decision'], 'enabled': True}
        backends = [llm_router._build_backend(dict(config, name=name)) for name in ('first', 'duplicate')]
        with patch.object(llm_router, 'load_backends', return_value=backends), \
             patch.object(llm_router, '_config', return_value={}):
            self.assertEqual([b.name for b in llm_router.route_backends('decision')], ['first'])

    def test_reassembles_json_and_preserves_token_metadata(self):
        rows = [{'message': {'content': '{"ok":'}, 'done': False},
                {'message': {'content': 'true}'}, 'done': False},
                {'done': True, 'done_reason': 'stop', 'eval_count': 7}]
        with tempfile.TemporaryDirectory() as folder, patch.object(transport, 'WORK', Path(folder)), \
             patch.object(transport.urllib.request, 'urlopen', return_value=io.BytesIO(
                 '\n'.join(json.dumps(row) for row in rows).encode())):
            response = transport.post_stream('http://localhost:11434/api/chat', {'model': 'fixture'})
            self.assertEqual(json.loads(response['message']['content']), {'ok': True})
            self.assertEqual(response['eval_count'], 7)
            self.assertEqual(json.loads((Path(folder) / 'local-inference-progress.json').read_text())['state'], 'complete')

    def test_truncated_stream_is_failure(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(transport, 'WORK', Path(folder)), \
             patch.object(transport.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"message":{"content":"partial"}}\n')):
            with self.assertRaises(RuntimeError):
                transport.post_stream('http://localhost:11434/api/chat', {'model': 'fixture'})
            self.assertEqual(json.loads((Path(folder) / 'local-inference-progress.json').read_text())['state'], 'failed')


if __name__ == '__main__':
    unittest.main()
