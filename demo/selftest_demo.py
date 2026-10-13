"""Meaningful protocol/confirmation/media tests. Cloud test is a mock, not a live API."""
import importlib.util, json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('demo',Path(__file__).with_name('server.py')); demo=importlib.util.module_from_spec(spec); spec.loader.exec_module(demo)
class Checks(unittest.TestCase):
    def test_plan_rejects_bad_cuts_and_subtitle_injection(self):
        for segment in [{'start':0,'end':float('nan')},{'start':0,'end':20},{'start':0,'end':1,'subtitle':'{\\pos(0,0)}'},{'start':0,'end':1,'subtitle':'x'*25}]:
            with self.assertRaises(ValueError): demo.validate_plan({'segments':[segment]},12)
    def test_cloud_wire_payload_and_truncation(self):
        captured={}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"facts":"visible"}'}}],'usage':{'total_tokens':12}}).encode()
        def request(req,timeout): captured.update(json.loads(req.data)); return Response()
        with patch.object(demo,'CLOUD_ENABLED',True),patch.object(demo,'KEY','test-only'),patch.object(demo.urllib.request,'urlopen',request):
            output,usage=demo.cloud([{'type':'video','video':['data:image/jpeg;base64,YQ==','data:image/jpeg;base64,Yg=='],'fps':2}],'observe')
        self.assertEqual(output['facts'],'visible');self.assertEqual(captured['messages'][0]['content'][1]['fps'],2);self.assertEqual(usage['total_tokens'],12)
    def test_sample_complete_media_loop(self):
        os.chdir(demo.ROOT)
        ident='testfixture001'
        demo.JOBS[ident]={'id':ident,'state':'running','path':str(demo.ROOT/'assets/source.mp4'),'mode':'sample','fps':2,'brief':'咖啡字幕短句','transcript':''}
        demo.analyze(ident); j=demo.JOBS[ident]
        self.assertEqual(j['state'],'review',j.get('stage'));self.assertEqual(j['media']['decoded_frames'],420);self.assertEqual(j['coverage']['sampled_frames'],28)
        self.assertEqual(j['plan_hash'],demo.digest(j['plan'])); demo.render(ident)
        self.assertEqual(j['state'],'done',j.get('stage'));self.assertEqual(j['qa']['decode'],'passed')
    def test_cloud_pipeline_requires_real_visual_observations(self):
        ident='testcloud001'
        demo.JOBS[ident]={'id':ident,'state':'running','path':str(demo.ROOT/'assets/source.mp4'),'mode':'cloud','fps':2,'brief':'咖啡字幕','transcript':''}
        plan={'title':'mock','summary':'protocol test','segments':[{'start':0,'end':4,'subtitle':'研磨','observation':'咖啡示意','reason':'动作推进'}]}
        with patch.object(demo,'cloud',side_effect=lambda content,prompt: ({'facts':'coffee','events':[]},{}) if content else (plan,{})) as mock:
            demo.analyze(ident)
        self.assertEqual(demo.JOBS[ident]['state'],'review');self.assertEqual(mock.call_count,3)
        self.assertEqual(mock.call_args_list[0].args[0][0]['type'],'video');self.assertIn('coffee',mock.call_args_list[-1].args[1])
if __name__=='__main__': unittest.main(verbosity=2)
