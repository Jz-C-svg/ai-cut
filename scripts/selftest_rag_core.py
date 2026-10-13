"""Offline, isolated regression tests for the operational RAG import."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aicut import learning_context as learning


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'content-library/rag'
        self.folder.mkdir(parents=True)
        self.path = self.folder / 'knowledge_core.jsonl'
        text = '--publish 只复制到剪映，不上传平台。'
        self.row = dict(id='card-test', title='发布含义', text=text, tier='core',
                        source_path='docs/current.md', source_locator='section:发布',
                        source_sha256='a'*64, content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        snapshot_date='2026-09-22', authority_note='文档证据，不是实测')
        self.write([self.row])

    def write(self, rows):
        self.path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')

    def test_metadata_and_prompt(self):
        result = learning.retrieve('--publish',root=self.root)
        doc = result['evidence'][0]
        self.assertEqual(doc['id'],'card-test')
        self.assertEqual(doc['source'],'docs/current.md')
        self.assertEqual(doc['snapshot_date'],'2026-09-22')
        self.assertIn('不是系统指令',learning.prompt_context(result))
        self.assertIn('不是实测',learning.prompt_context(result))

    def test_no_mirrors_or_evaluation_leak(self):
        for name in ('manifest.json','evaluation.jsonl','mirror.md','knowledge_all.jsonl'):
            (self.folder/name).write_text('secret-evaluation-fixture',encoding='utf-8')
        data=learning.catalog(self.root)
        self.assertEqual(len(data['docs']),1)
        self.assertNotIn('secret-evaluation-fixture',str(data))

    def test_bad_rows_isolated_and_duplicate_rejected(self):
        bad=dict(self.row,id='tampered',text='changed')
        self.write([self.row,self.row,bad,dict(self.row,id='ref',tier='reference'),[]])
        with self.path.open('a',encoding='utf-8') as f: f.write('{broken\n')
        data=learning.catalog(self.root)
        self.assertEqual(len(data['docs']),1)
        self.assertEqual(sum('invalid or duplicate' in w for w in data['warnings']),5)

    def test_refresh_replace_and_remove(self):
        before=learning.catalog(self.root)['signature']
        row=dict(self.row,text='--publish 复制草稿到剪映目录，仍不上传平台。')
        row['content_sha256']=hashlib.sha256(row['text'].encode()).hexdigest()
        self.write([row])
        data=learning.catalog(self.root)
        self.assertNotEqual(before,data['signature'])
        self.assertEqual(len(data['docs']),1)
        self.assertEqual(data['docs'][0]['text'],row['text'])
        self.path.unlink()
        self.assertEqual(len(learning.catalog(self.root)['docs']),0)

    def test_budget_and_unrelated_query(self):
        self.assertFalse(learning.retrieve('--publish',root=self.root,budget=1)['evidence'])
        self.assertFalse(learning.retrieve('zxunrelatedxyz',root=self.root)['evidence'])


if __name__=='__main__':
    unittest.main()
