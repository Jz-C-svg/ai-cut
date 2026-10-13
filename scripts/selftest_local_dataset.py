"""Synthetic invariants for collection; these do not rate real media quality."""
import tempfile, unittest, wave, json
from pathlib import Path
import numpy as np
from collect_local_dataset import audio_metrics,collect,digest
from search_local_dataset import search_dataset

class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source=self.root/'source';self.source.mkdir();self.out=self.root/'dataset';self.out.mkdir()
    def tearDown(self):self.tmp.cleanup()
    def audio(self,right=1):
        p=self.source/'test.wav';v=np.sin(np.arange(16000)*2*np.pi*440/16000)*.2
        x=np.column_stack([v,v*right]);
        with wave.open(str(p),'wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(16000);w.writeframes((x*32767).astype('<i2').tobytes())
        return p
    def test_center(self):
        a=audio_metrics(self.audio(),2);self.assertAlmostEqual(a['balance_db'],0,places=5);self.assertAlmostEqual(a['correlation'],1,places=5);self.assertAlmostEqual(a['mono_fold_loss_db'],0,places=5)
    def test_inverted(self):
        a=audio_metrics(self.audio(-1),2);self.assertLess(a['correlation'],-.99);self.assertLess(a['mono_fold_loss_db'],-80)
    def test_imbalance(self):
        a=audio_metrics(self.audio(.5),2);self.assertAlmostEqual(a['balance_db'],6.02,places=2)
    def test_original_unchanged_and_cache(self):
        p=self.audio();before=digest(p);a=collect(p,self.source,self.out);b=collect(p,self.source,self.out);self.assertEqual(a,b);self.assertEqual(before,digest(p));self.assertEqual(a['status'],'ok');self.assertEqual(a['review_status'],'unreviewed')
    def test_corrupt_image_recorded(self):
        p=self.source/'bad.jpg';p.write_bytes(b'not an image');r=collect(p,self.source,self.out);self.assertEqual(r['status'],'error');self.assertIn('error',r)
    def search_fixture(self):
        t=self.out/'transcript.json';t.write_text(json.dumps({'segments':[{'text':'今天讲房间','start':1,'end':2}]}),encoding='utf-8')
        r={'file':'original.mp4','relative':'腹直肌.mp4','sha256':'abc','kind':'video','status':'ok','issues':[],'transcript':str(t)}
        (self.out/'manifest.json').write_text(json.dumps([r,{**r,'file':'copy.mp4','duplicate_of':'original.mp4'}]),encoding='utf-8')
    def test_filename_match_has_no_invented_timestamp(self):
        self.search_fixture();results=search_dataset(self.out,'腹直肌');self.assertEqual(len(results),1);self.assertIsNone(results[0]['start']);self.assertEqual(results[0]['basis'],'filename_only')
    def test_speech_match_returns_original_time(self):
        self.search_fixture();results=search_dataset(self.out,'房间');self.assertEqual(len(results),1);self.assertEqual(results[0]['start'],1);self.assertEqual(results[0]['file'],'original.mp4')

if __name__=='__main__':unittest.main()
