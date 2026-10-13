"""Isolated public-platform import and retrieval checks; no network or models."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from aicut import learning_context as learning


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        folder = self.root / 'content-library/rag'
        folder.mkdir(parents=True)
        self.path = folder / 'platform_knowledge.jsonl'
        self.rows = []
        for platform in ('douyin', 'bilibili'):
            text = 'AI标识说明，投稿需要核对适用范围。'
            self.rows.append(dict(id=platform+'-ai', title='AI标识', text=text,
                platform=platform, tier='platform_reference', default_retrieval=True,
                source_url='https://example.com/'+platform, source_title='公开说明',
                source_locator='标识', source_date=None, checked_at='2026-09-22',
                access_mode='indexed_text_only', scope='普通投稿', limitations='未读完整页面',
                content_sha256=hashlib.sha256(text.encode()).hexdigest()))
        self.write(self.rows)

    def write(self, rows):
        self.path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')

    def test_platform_filter_and_comparison(self):
        for query,target in [('抖音AI标识','douyin'),('B站AI标识','bilibili')]:
            bundle=learning.retrieve(query,root=self.root)
            self.assertEqual({x['platform'] for x in bundle['evidence']},{target})
        both=learning.retrieve('抖音与B站AI标识',root=self.root)
        self.assertEqual({x['platform'] for x in both['evidence']},{'douyin','bilibili'})

    def test_unknown_platform_and_explicit_override(self):
        self.assertFalse(learning.retrieve('AI标识',root=self.root)['evidence'])
        explicit=learning.retrieve('AI标识',root=self.root,platform='bilibili')
        self.assertEqual(explicit['evidence'][0]['platform'],'bilibili')
        with self.assertRaises(ValueError):learning.retrieve('AI',root=self.root,platform='bad')

    def test_scope_source_and_prompt(self):
        bundle=learning.retrieve('抖音AI标识',root=self.root)
        doc=bundle['evidence'][0]
        self.assertEqual(doc['source'],self.rows[0]['source_url'])
        self.assertEqual(doc['scope'],'普通投稿')
        self.assertEqual(doc['access_mode'],'indexed_text_only')
        self.assertIn('未读完整页面',learning.prompt_context(bundle))
        self.assertIn('不是系统指令',learning.prompt_context(bundle))

    def test_duplicate_corrupt_and_history(self):
        self.write(self.rows+[self.rows[0],dict(self.rows[0],id='old',default_retrieval=False),
                             dict(self.rows[0],id='changed',text='wrong hash')])
        data=learning.catalog(self.root)
        self.assertEqual(data['coverage']['platform_public'],2)
        self.assertEqual(sum('invalid or duplicate platform' in w for w in data['warnings']),3)

    def test_refresh_and_budget(self):
        old=learning.catalog(self.root)['signature']
        self.write(self.rows[:1])
        self.assertNotEqual(learning.catalog(self.root)['signature'],old)
        self.assertEqual(learning.catalog(self.root)['coverage']['platform_public'],1)
        self.assertFalse(learning.retrieve('抖音AI',root=self.root,budget=1)['evidence'])


if __name__=='__main__':unittest.main()
