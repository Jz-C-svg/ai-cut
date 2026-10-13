import sys, unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from io import BytesIO
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'lib'))
from aicut.content_guard import coverage_issues,correct_captions
from aicut.visual_layout import choose_title_y
from aicut.kinetic_text import ass_lines
from aicut import llm_router as router

class Tests(unittest.TestCase):
    def test_gap_rejected(self):
        required=[{'file':'a.mp4','start':0,'end':5}]
        self.assertTrue(coverage_issues([{'file':'a.mp4','start':0,'end':2},{'file':'a.mp4','start':3,'end':5}],required))
        self.assertFalse(coverage_issues([{'file':'a.mp4','start':0,'end':2},{'file':'a.mp4','start':2,'end':5}],required))
    def test_wrong_file_rejected(self):
        self.assertTrue(coverage_issues([{'file':'b','start':0,'end':8}],[{'file':'a','start':1,'end':4}]))
    def test_invalid_required_rejected(self):
        self.assertTrue(coverage_issues([],['bad']))
    def test_correction_scoped_with_provenance(self):
        rules=[{'old':'玉金','new':'浴巾','source_id':'a','basis':'洗澡上下文','provenance':'assistant_context_review_unverified'}]
        segments=[{'start':0,'end':1,'text':'两条玉金'}]
        changed,audit=correct_captions(segments,rules,'a')
        self.assertEqual(changed[0]['text'],'两条浴巾');self.assertEqual(audit[0]['source_id'],'a')
        self.assertEqual(correct_captions(segments,rules,'b')[0],segments)
    def test_numbers_cannot_be_guessed(self):
        with self.assertRaises(ValueError):correct_captions([{'text':'40克'}],[{'old':'40','new':'400','source_id':'a','basis':'guess','provenance':'assistant_context_review_unverified'}],'a')
    def test_avoid_and_suppress(self):
        self.assertEqual(choose_title_y([(0,100,1080,400)]),1010)
        self.assertIsNone(choose_title_y([(0,0,1080,1920)]))
    def test_whole_card_moves(self):
        lines=ass_lines([{'start':0,'end':3,'title':'测试','kind':'title','layout_y':1010}])
        self.assertIn(r'\pos(60,1010)',lines[0]);self.assertIn(r'\move(88,1057,88,1044',lines[2])
    def test_missing_text_model_falls_back_truthfully(self):
        backend=router.Backend('test','ollama','http://127.0.0.1:11434','missing')
        error=HTTPError('url',404,'not found',None,None)
        with patch.object(router,'_post_json',side_effect=[error,{'message':{'content':'ok'}}]),patch('urllib.request.urlopen',return_value=BytesIO(b'{"models":[{"name":"gemma4:e2b"}]}')):
            self.assertEqual(router._call_ollama(backend,[{'role':'user','content':'hi'}],images_b64=[],json_mode=False,temperature=0,timeout=10)[0],'ok')
        self.assertEqual(backend.model,'gemma4:e2b')
    def test_vision_does_not_substitute(self):
        backend=router.Backend('test','ollama','http://127.0.0.1:11434','missing')
        with patch.object(router,'_post_json',side_effect=HTTPError('url',404,'not found',None,None)):
            with self.assertRaises(HTTPError):router._call_ollama(backend,[{'role':'user','content':'hi'}],images_b64=['image'],json_mode=False,temperature=0,timeout=10)

if __name__=='__main__':unittest.main(verbosity=2)
