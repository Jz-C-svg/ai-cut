import unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'lib'));sys.path.insert(0,str(ROOT/'scripts'))
from aicut.material_screening import screen_transcript,parse_signals,planning_constraints
import cleanup

def seg(text,a=0,b=1):return {'text':text,'start':a,'end':b}
class Tests(unittest.TestCase):
    def test_command(self):
        self.assertEqual(screen_transcript([seg('1，2，3，开始')],2)[0]['action'],'auto_remove')
    def test_instruction_numbers_not_command(self):
        self.assertEqual(screen_transcript([seg('准备123个用品')],2),[])
    def test_bare_count_review(self):
        self.assertEqual(screen_transcript([seg('123')],2)[0]['action'],'review')
    def test_split_count(self):
        hits=screen_transcript([seg('1'),seg('2',1,2),seg('3',2,3),seg('开始',3,4)],5)
        self.assertTrue(any(h['start']==0 and h['end']==4 and h['action']=='auto_remove' for h in hits))
    def test_join_has_distinct_source_start_and_end(self):
        keeps=[[0,1],[2,3]]
        self.assertEqual(cleanup.clean_to_source(1,keeps,endpoint='start'),2)
        self.assertEqual(cleanup.clean_to_source(1,keeps,endpoint='end'),1)
    def test_good_dialogue_survives(self):
        self.assertEqual(cleanup.find_cut_ranges([seg('好')],cleanup.load_rules(),2),[])
    def test_repetition(self):
        hits=screen_transcript([seg('今天我们来介绍准备清单'),seg('今天我们来介绍准备清单',1.2,2.2)],3)
        self.assertEqual(hits[0]['action'],'auto_remove')
    def test_repeated_numbers_review(self):
        hits=screen_transcript([seg('需要准备三条纱布浴巾'),seg('需要准备三条纱布浴巾',1.2,2.2)],3)
        self.assertEqual(hits[0]['action'],'review')
    def test_negation_difference_review(self):
        hits=screen_transcript([seg('我们需要准备这个用品'),seg('我们不需要准备这个用品',1.2,2.2)],3)
        self.assertFalse(any(h['action']=='auto_remove' for h in hits))
    def test_protected_repeat(self):
        hits=screen_transcript([seg('今天我们来介绍准备清单'),seg('今天我们来介绍准备清单',1.2,2.2)],3,protected=[(0,1)])
        self.assertEqual(hits[0]['action'],'review')
    def test_short_keep_and_original_unchanged(self):
        cfg=cleanup.load_rules();self.assertEqual(cleanup.keep_ranges([[.2,1]],2,cfg),[[0.,.2],[1,2]])
        source=[seg('有用内容')];copy=cleanup.drop_hidden(source,[]);copy[0]['start']=99;self.assertEqual(source[0]['start'],0)
    def test_open_freeze_and_silence(self):
        hits=parse_signals('freeze_start: 1\nsilence_start: 2\nsilence_end: 3\nblack_start:4 black_end:5',6)
        self.assertEqual(hits[0],{'start':1.,'end':6,'kind':'freeze','action':'review'})
        self.assertEqual(len(hits),3)
    def test_invalid_timestamp(self):
        with self.assertRaises(ValueError):screen_transcript([seg('hi',float('nan'),2)],3)
    def test_planner_enforces_filter(self):
        media=[{'id':'m01','duration':3,'segments':[seg('1 2 3 开始'),seg('独有补充内容',1,2)]}]
        review=planning_constraints(media,{})
        self.assertEqual(review['excluded_ranges'][0]['first_segment'],1)
        self.assertEqual(review['excluded_ranges'][0]['last_segment'],1)
    def test_planner_protects_content_unit(self):
        media=[{'id':'m01','duration':3,'segments':[seg('1 2 3 开始'),seg('独有补充内容',1,2)]}]
        review={'content_units':[{'ranges':[{'media_id':'m01','first_segment':1,'last_segment':1}]}]}
        result=planning_constraints(media,review)
        self.assertEqual(result['excluded_ranges'],[]);self.assertNotIn('excluded_ranges',review)

if __name__=='__main__':unittest.main(verbosity=2)
