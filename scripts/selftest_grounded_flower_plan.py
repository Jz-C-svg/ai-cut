import copy,unittest
from grounded_flower_plan import check_plan,materialize
E={'source':'test','source_sha256':'x','segments':[{'id':f's{i}','start':i,'end':i+1,'text':'先平躺然后抬头'} for i in range(4)],'styles':[{'id':'style'}]}
def plan(): return {'groups':[{'segment_ids':[f's{i}'],'role':'动作演示','flower':None} for i in range(4)]}
def flower(sid='s0',text='先平躺'): return {'text':text,'segment_id':sid,'style_reference_id':'style'}
class Tests(unittest.TestCase):
    def test_malformed_types_fail_closed(self):
        p=plan();p['groups'][0]['role']=[];self.assertTrue(check_plan(p,E))
        p=plan();p['groups'][0]['flower']=flower();p['groups'][0]['flower']['style_reference_id']={};self.assertTrue(check_plan(p,E))
    def test_valid(self): self.assertEqual(check_plan(plan(),E),[])
    def test_omission(self):
        p=plan();p['groups'].pop();self.assertTrue(check_plan(p,E))
    def test_duplicate(self):
        p=plan();p['groups'][1]['segment_ids']=['s0'];self.assertTrue(check_plan(p,E))
    def test_wrong_group(self):
        p=plan();p['groups'][0]['flower']=flower('s2');self.assertTrue(check_plan(p,E))
    def test_invented_location(self):
        p=plan();p['groups'][0]['flower']=flower(text='位置三：腹下');self.assertTrue(check_plan(p,E))
    def test_uncertain_asr(self):
        p=plan();p['groups'][0]['flower']=flower();self.assertTrue(check_plan(p,E,{'s0'}))
    def test_density(self):
        p=plan()
        for i,g in enumerate(p['groups']): g['flower']=flower(f's{i}')
        self.assertTrue(check_plan(p,E))
    def test_invented_title_or_time(self):
        p=plan();p['title']='修复方案';self.assertTrue(check_plan(p,E))
        p=plan();p['groups'][0]['start']=9;self.assertTrue(check_plan(p,E))
    def test_no_automatic_release(self):
        r=materialize(plan(),E,set());self.assertFalse(r['release_ready']);self.assertEqual(r['groups'][0]['source_start'],0)
    def test_nonexistent_style(self):
        p=plan();p['groups'][0]['flower']=flower();p['groups'][0]['flower']['style_reference_id']='invented';self.assertTrue(check_plan(p,E))
if __name__=='__main__': unittest.main()
