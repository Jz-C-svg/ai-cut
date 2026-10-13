"""Offline LAN-token tests; uses a temporary config and never starts a listener."""
import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import server

class AppSecurityTests(unittest.TestCase):
    def test_weak_token_rotated_and_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json'
            config,rotated=server.prepare_lan_token({'host':'0.0.0.0','token':'aicut'},path)
            self.assertTrue(rotated);self.assertNotEqual(config['token'],'aicut')
            self.assertGreaterEqual(len(config['token']),40)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['token'],config['token'])

    def test_strong_custom_token_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'not-created.json';token='custom-'+('x'*40)
            config,rotated=server.prepare_lan_token({'token':token},path)
            self.assertFalse(rotated);self.assertEqual(config['token'],token);self.assertFalse(path.exists())

    def test_missing_and_short_tokens_are_rotated(self):
        with tempfile.TemporaryDirectory() as d:
            for i,token in enumerate((None,'short')):
                path=Path(d)/f'{i}.json';config,rotated=server.prepare_lan_token({'token':token},path)
                self.assertTrue(rotated);self.assertGreaterEqual(len(config['token']),40)

    def test_non_loopback_bind_detection(self):
        for host,expected in [('127.0.0.1',False),('::1',False),('localhost',False),('0.0.0.0',True),('192.168.1.2',True),('',False)]:
            with self.subTest(host=host):self.assertEqual(server.is_lan_bind(host),expected)

if __name__=='__main__':unittest.main(verbosity=2)
