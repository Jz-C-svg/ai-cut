"""Synthetic media acceptance tests; no live model or buyer quality claims."""
import json, os, unittest
from pathlib import Path
import server as demo
import delivery
class DeliveryChecks(unittest.TestCase):
 def test_review_states_and_stale_hashes(self):
  current={'technical_status':'passed','output_sha256':'out','plan_sha256':'plan','human_review':{k:'pending' for k in ('subtitle_readability','visual_quality','audio_sync','facts_and_rights')}}
  body={'output_sha256':'out','plan_sha256':'plan','reviewer':'test fixture','checks':dict(current['human_review'])}
  self.assertEqual(delivery.accept(current,body)['acceptance_status'],'pending')
  body['checks']={k:'passed' for k in body['checks']};self.assertEqual(delivery.accept(current,body)['acceptance_status'],'accepted')
  body['checks']['visual_quality']='needs_fix';self.assertEqual(delivery.accept(current,body)['acceptance_status'],'needs_fix')
  body['output_sha256']='stale'
  with self.assertRaises(ValueError):delivery.accept(current,body)
 def test_three_media_shapes(self):
  os.chdir(demo.ROOT);cases=[]
  for name,seconds,filters,audio in [('portrait',14,None,True),('landscape',6,'scale=360:640,pad=1138:640:(ow-iw)/2:(oh-ih)/2',True),('silent',8,None,False)]:
   ident='delivery-test-'+name;folder=demo.WORK/ident;folder.mkdir(exist_ok=True);source=demo.ROOT/'assets/source.mp4'
   if name!='portrait':
    source=folder/'input.mp4';args=['ffmpeg','-v','error','-y','-i',str(demo.ROOT/'assets/source.mp4'),'-t',str(seconds)]
    if filters:args+=['-vf',filters]
    if not audio:args+=['-an']
    demo.run(args+['-c:v','libx264','-preset','fast','-crf','23',str(source)])
   plan=demo.validate_plan({'title':name,'summary':'Synthetic technical fixture','segments':[{'start':0,'end':seconds,'subtitle':'咖啡的片刻','observation':'AI coffee imagery','reason':'technical fixture'}]},seconds)
   j={'id':ident,'state':'review','path':str(source),'mode':'manual','synthetic_source':True,'source_digest':delivery.sha(source),'media':{'audio':audio},'plan':plan,'plan_hash':demo.digest(plan),'plan_version':1};demo.JOBS[ident]=j;demo.render(ident)
   self.assertEqual(j['state'],'done',j.get('stage'));self.assertEqual(j['acceptance']['technical_status'],'passed');self.assertEqual(j['acceptance']['acceptance_status'],'pending')
   cases.append({'case':name,'source_provenance':'synthetic derivative of generated coffee footage','model_called':False,'report':j['acceptance']})
  (demo.ROOT/'examples/case-matrix.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':unittest.main(verbosity=2)
