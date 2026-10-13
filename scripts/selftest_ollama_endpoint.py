"""Endpoint consistency and local-only fallback regressions; no model requests."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'scripts')]
from aicut import ollama_endpoint as endpoint, paths, llm_router
import understand


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aicut-endpoint-')
        self.directory = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'AICUT_OLLAMA_BASE_URL': ''})
        self.env.start()
        self.config = patch.object(paths, 'CONFIG_DIR', self.directory)
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.env.stop()
        self.tmp.cleanup()

    def write_config(self, value):
        (self.directory / 'aicut.json').write_text(json.dumps(value), encoding='utf-8')

    def test_configured_endpoint_used_for_discovery_and_inference(self):
        self.write_config({'ollama_base_url': 'http://127.0.0.1:11436/'})
        for path in ('/api/chat', '/api/show', '/api/tags', '/api/embeddings'):
            self.assertEqual(endpoint.ollama_url(path), 'http://127.0.0.1:11436' + path)

    def test_environment_override_wins(self):
        self.write_config({'ollama_base_url': 'http://127.0.0.1:11436'})
        with patch.dict(os.environ, {'AICUT_OLLAMA_BASE_URL': 'http://localhost:11438'}):
            self.assertEqual(endpoint.ollama_base_url(), 'http://localhost:11438')

    def test_missing_configuration_keeps_standard_port(self):
        self.assertEqual(endpoint.ollama_base_url(), endpoint.DEFAULT_BASE_URL)

    def test_bad_base_urls_rejected(self):
        for value in ('ftp://localhost', 'http://user:secret@localhost',
                      'http://localhost/api/chat', 'http://localhost?key=x',
                      'http://localhost#secret', 'localhost:11436'):
            with self.subTest(value=value), patch.dict(os.environ, {'AICUT_OLLAMA_BASE_URL': value}):
                with self.assertRaises(ValueError):
                    endpoint.ollama_base_url()
        with self.assertRaises(ValueError):
            endpoint.ollama_url('/chat/completions')

    def test_router_override_only_changes_ollama(self):
        with patch.dict(os.environ, {'AICUT_OLLAMA_BASE_URL': 'http://127.0.0.1:11436'}):
            local = llm_router._build_backend({'type': 'ollama', 'base_url': 'http://localhost:11434'})
            remote = llm_router._build_backend({'type': 'openai', 'base_url': 'https://example.invalid/v1'})
        self.assertEqual(local.base_url, 'http://127.0.0.1:11436')
        self.assertEqual(remote.base_url, 'https://example.invalid/v1')

    def test_router_missing_base_uses_project_address(self):
        self.write_config({'ollama_base_url': 'http://127.0.0.1:11436'})
        self.assertEqual(llm_router._build_backend({'type': 'ollama'}).base_url,
                         'http://127.0.0.1:11436')

    def test_legacy_auto_cannot_fall_back_to_paid_api_when_disabled(self):
        with patch.object(llm_router, '_config', return_value={'ai_router': {'global_paid_api_enabled': False}}), \
             patch.object(llm_router, 'route_backends', return_value=[]), \
             patch.object(understand, 'load_key', side_effect=AssertionError('must not read key')), \
             patch.object(understand, 'post_json', side_effect=ConnectionError('offline')) as post:
            self.assertEqual(understand.llm_json('test', 'auto'), (None, ''))
        self.assertEqual(post.call_count, 1)
        self.assertIn('/api/chat', post.call_args.args[0])

    def test_explicit_paid_request_also_respects_disabled_flag(self):
        with patch.object(llm_router, '_config', return_value={'ai_router': {'global_paid_api_enabled': False}}), \
             patch.object(understand, 'post_json') as post, patch.object(understand, 'load_key') as key:
            self.assertEqual(understand.llm_json('test', 'deepseek'), (None, ''))
        post.assert_not_called()
        key.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
