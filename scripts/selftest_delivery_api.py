"""Loopback HTTP delivery contract tests; never launch a render or call a model."""
import json
import hashlib
import subprocess
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'lib')]
import server


class DeliveryAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_dir = tempfile.TemporaryDirectory(prefix='aicut_api_media_')
        cls.source = Path(cls.media_dir.name) / 'source.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=180x320:r=30:d=1',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(cls.source)], check=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.media_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aicut_api_')
        self.root = Path(self.temp.name)
        self.local_source = self.root / 'source.mp4'
        shutil.copy2(self.source, self.local_source)
        self.sheet = self.root / 'approved.json'
        self.sheet.write_text(json.dumps({'project': 'api-regression', 'requires_plan_confirmation': True,
                                         'clips': [{'file': str(self.local_source), 'start': 0, 'end': 1}]}), encoding='utf-8')
        self.patches = [patch.object(server, 'PROJECT', self.root),
                        patch.object(server, 'CONFIG', {'token': 'offline-test-token', 'allowed_roots': [str(self.root)]})]
        for item in self.patches:
            item.start()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(2)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self, path, body=None, authorized=True):
        headers = {'Content-Type': 'application/json'}
        if authorized:
            headers['X-Auth-Token'] = 'offline-test-token'
        req = urllib.request.Request(self.base + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_capability_endpoint_auth_and_truthful_status(self):
        self.assertEqual(self.request('/api/editors', authorized=False)[0], 401)
        code, data = self.request('/api/editors')
        self.assertEqual(code, 200)
        self.assertEqual(len(data['editors']), 7)
        self.assertTrue(all(not e['host_verified'] and not e['native_auto_render'] for e in data['editors']))

    def test_queue_preserves_approval_and_report_is_readable(self):
        digest = hashlib.sha256(self.sheet.read_bytes()).hexdigest()
        with patch.object(server, 'start_task') as start:
            code, data = self.request('/api/delivery', {'sheet': str(self.sheet), 'software': 'premiere',
                                                       'engine': 'portable', 'approve_plan_sha256': digest})
        self.assertEqual(code, 200)
        cmd = start.call_args.args[1]
        self.assertEqual(cmd[cmd.index('--approve-plan-sha256') + 1], digest)
        self.assertEqual(cmd[cmd.index('--engine') + 1], 'portable')
        self.assertNotIn('--publish', cmd)
        report = Path(cmd[cmd.index('--report-out') + 1])
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({'status': 'rendered_pending_visual_review', 'executed_in_editor': False}))
        code, delivery = self.request(data['report_url'])
        self.assertEqual(code, 200)
        self.assertFalse(delivery['executed_in_editor'])

    def test_rejects_paths_outside_allowed_root_without_queueing(self):
        with patch.object(server, 'start_task') as start:
            code, _ = self.request('/api/delivery', {'sheet': str(self.sheet), 'out': str(self.root.parent / 'outside.mp4')})
        self.assertEqual(code, 403)
        start.assert_not_called()

    def test_review_hash_covers_exact_bom_and_crlf_bytes(self):
        raw = b'\xef\xbb\xbf' + json.dumps(json.loads(self.sheet.read_text()), indent=2).replace('\n','\r\n').encode()
        self.sheet.write_bytes(raw)
        query = urllib.parse.urlencode({'sheet': str(self.sheet), 'software': 'jianying', 'engine': 'project'})
        code, reviewed = self.request('/api/delivery-plan?' + query)
        self.assertEqual(code, 200)
        self.assertTrue(reviewed['ready'])
        self.assertEqual(reviewed['sheet_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(reviewed['summary']['duration'], 1)

    def test_stale_or_missing_confirmation_cannot_queue(self):
        with patch.object(server, 'start_task') as start:
            for digest in (None, 'old-version'):
                code, _ = self.request('/api/delivery', {'sheet': str(self.sheet), 'approve_plan_sha256': digest})
                self.assertEqual(code, 409)
            valid = hashlib.sha256(self.sheet.read_bytes()).hexdigest()
            self.sheet.write_text(self.sheet.read_text() + ' ')
            self.assertEqual(self.request('/api/delivery', {'sheet': str(self.sheet), 'approve_plan_sha256': valid})[0], 409)
        start.assert_not_called()

    def test_unsupported_native_adapter_cannot_queue(self):
        with patch.object(server, 'start_task') as start:
            code, data = self.request('/api/delivery', {'sheet': str(self.sheet), 'software': 'premiere',
                                                      'engine': 'project', 'approve_plan_sha256': hashlib.sha256(self.sheet.read_bytes()).hexdigest()})
        self.assertEqual(code, 422)
        self.assertTrue(data['blockers'])
        start.assert_not_called()

    def test_legacy_export_cannot_bypass_review(self):
        with patch.object(server, 'start_task') as start:
            self.assertEqual(self.request('/api/export', {'sheet': str(self.sheet)})[0], 409)
        start.assert_not_called()

    def test_duration_limit_remains_a_blocker(self):
        data = json.loads(self.sheet.read_text()); data['clips'] *= 2; data['duration_limit'] = 1
        self.sheet.write_text(json.dumps(data))
        query = urllib.parse.urlencode({'sheet': str(self.sheet)})
        _, reviewed = self.request('/api/delivery-plan?' + query)
        self.assertFalse(reviewed['ready'])
        self.assertTrue(any('总时长' in p for p in reviewed['blockers']))

    def test_style_index_resolves_parameter_details_and_reports_missing_cards(self):
        library = self.root / 'content-library'; library.mkdir()
        (library / 'styles.json').write_text(json.dumps({'version': '2.0', 'styles': [
            {'id':'valid', '文件':'valid.json'}, {'id':'missing', '文件':'absent.json'}]}))
        (library / 'valid.json').write_text(json.dumps({'id':'valid', '剪辑参数': {'镜头最短秒': 2}}))
        code, data = self.request('/api/library?kind=styles')
        self.assertEqual(code, 200)
        self.assertEqual(data['styles'][0]['剪辑参数']['镜头最短秒'], 2)
        self.assertEqual(len(data['styles']), 1)
        self.assertEqual(len(data['warnings']), 1)

    def test_async_planning_never_builds_or_publishes_before_confirmation(self):
        with patch.object(server, 'start_task', return_value='plan-task') as start:
            code, data = self.request('/api/autoedit', {'video': str(self.local_source),
                                                       'async': True, 'plan_only': False})
        self.assertEqual(code, 200)
        self.assertEqual(data['task'], 'plan-task')
        self.assertIn('--no-build', start.call_args.args[1])
        self.assertNotIn('--publish', start.call_args.args[1])
        self.assertIn('/api/production/', data['report_url'])

    def test_invalid_payload_or_editor_does_not_queue(self):
        with patch.object(server, 'start_task') as start:
            for body in ([], {'sheet': 123}, {'sheet': str(self.sheet), 'software': 'unknown'}):
                with self.subTest(body=body):
                    self.assertEqual(self.request('/api/delivery', body)[0], 400)
        start.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
