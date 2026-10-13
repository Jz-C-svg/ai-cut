import unittest,tempfile,json,subprocess,hashlib
from pathlib import Path
from collect_production_visual import parse_frames,union_duration,build_timeline,collect
from collect_visual_text import layout
from search_production_visual import search

class VisualTests(unittest.TestCase):
    def test_metadata_times(self):
        frames=parse_frames('frame:0 pts:0 pts_time:0\nlavfi.scene_score=0.000000\nframe:1 pts:50 pts_time:2\nlavfi.scene_score=0.800000')
        self.assertEqual(frames[1],{'number':2,'time':2.,'scene_score':.8})
    def test_speech_union_not_double_counted(self):
        self.assertEqual(union_duration([(-1,2),(1,3),(4,9)],5),4)
    def test_cut_bounds(self):
        cuts,shots=build_timeline([{'time':0,'scene_score':1},{'time':2,'scene_score':.8},{'time':2.1,'scene_score':.8},{'time':4,'scene_score':.8}],4,.3)
        self.assertEqual(cuts,[2]);self.assertEqual(sum(s['duration'] for s in shots),4)
    def test_ocr_geometry(self):
        r=layout([[10,10],[30,10],[30,20],[10,20]],100,100)
        self.assertEqual(r['bbox_normalized'],[.1,.1,.3,.2]);self.assertEqual(r['vertical_zone'],'upper');self.assertAlmostEqual(r['relative_height'],.1)
    def test_frame_search_keeps_evidence_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);row={'source':'a.mp4','time':2.5,'screen_text':'宝宝训练','speech_text':'','large_text_candidate':True}
            (root/'frame-index.jsonl').write_text(json.dumps(row),encoding='utf-8')
            result=search(root,'宝宝');self.assertEqual(len(result),1);self.assertEqual(result[0]['time'],2.5);self.assertEqual(search(root,'房间'),[])
    def test_large_text_filter(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);row={'source':'a.mp4','time':2.5,'screen_text':'宝宝','speech_text':'','large_text_candidate':False}
            (root/'frame-index.jsonl').write_text(json.dumps(row),encoding='utf-8');self.assertEqual(search(root,'宝宝',large_text=True),[])
    def test_real_cut_and_source_preservation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source.mkv'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=320x240:r=25:d=2','-f','lavfi','-i','color=blue:s=320x240:r=25:d=2','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','ffv1','-y',str(source)],capture_output=True,check=True)
            sha=hashlib.sha256(source.read_bytes()).hexdigest();st=source.stat()
            row={'file':str(source),'sha256':sha,'bytes':st.st_size,'mtime_ns':st.st_mtime_ns,'duration':4,'metadata':{'streams':[{'codec_type':'video'}]}}
            result=collect(row,root/'dataset',.3,5)
            self.assertEqual(result['candidate_cuts'],[2.]);self.assertEqual(len(result['frames']),2)
            self.assertEqual(sha,hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertTrue(all(Path(f['file']).exists() for f in result['frames']))

if __name__=='__main__':unittest.main()
