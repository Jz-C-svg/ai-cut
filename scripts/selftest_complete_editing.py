import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'lib'));sys.path.insert(0,str(ROOT/'scripts'))
from aicut.media_catalog import index_library,search,choose_broll,map_anchor,probe_asset
from aicut.kinetic_text import compile_cards,ass_lines
from aicut.professional_editing import validate_constraints,content_coverage,polish_captions
import export_video,build_draft

class CompleteEditingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.group=self.root/'腹直肌';self.group.mkdir()
        self.path=self.group/'detail.MOV';self.path.write_bytes(b'fixture')
        st=self.path.stat();self.annotation={str(self.path):{'fingerprint':{'size':st.st_size,'mtime_ns':st.st_mtime_ns},
            'description':'腹部手指特写','tags':['腹直肌','手指'],'reviewed':True,
            'usable_ranges':[{'start':1,'end':5,'roles':['detail']}],'review_note':'fixture'}}
        self.catalog=index_library(self.root,annotations=self.annotation)
        self.clips=[{'media_id':'m01','start':10,'end':20,'speed':1},{'media_id':'m02','start':5,'end':15,'speed':1}]
    def test_query_relevance(self):
        self.assertEqual(len(search(self.catalog,'腹直肌 特写')),1)
        self.assertEqual(search(self.catalog,'汽车发动机'),[])

    def test_asr_search_does_not_authorize_broll_and_expires(self):
        self.annotation[str(self.path)].update(transcript_text='独有补充口水巾',transcript_basis='automatic_asr_unreviewed',reviewed=False)
        catalog=index_library(self.root,annotations=self.annotation)
        self.assertEqual(len(search(catalog,'口水巾')),1)
        self.assertEqual(search(catalog,'口水巾',reviewed_only=True),[])
        self.path.write_bytes(b'changed media')
        catalog=index_library(self.root,previous=catalog,annotations=self.annotation)
        self.assertEqual(search(catalog,'口水巾'),[])
    def test_filename_not_visual_review(self):
        self.assertEqual(search(index_library(self.root),'腹直肌',reviewed_only=True),[])
    def test_incremental_preserves_review(self):
        self.assertTrue(index_library(self.root,self.catalog)['assets'][0]['reviewed'])
    def test_media_change_invalidates_review(self):
        self.path.write_bytes(b'changed-data')
        row=index_library(self.root,self.catalog,self.annotation)['assets'][0]
        self.assertFalse(row['reviewed'])
        with self.assertRaises(ValueError):probe_asset(self.catalog['assets'][0])
    def test_deleted_asset_disappears(self):
        self.path.unlink();self.assertEqual(index_library(self.root,self.catalog)['assets'],[])
    def test_matching_broll_duration_and_reason(self):
        request={'query':'腹直肌','role':'detail','at':2,'duration':3,'reason':'解释手指位置'}
        with patch('aicut.media_catalog.probe_asset',return_value={'format':{'duration':'10'}}):
            chosen=choose_broll(self.catalog,request,20)
        self.assertEqual((chosen['start'],chosen['end']),(1,4));self.assertEqual(chosen['reason'],request['reason'])
    def test_no_implicit_freeze_for_short_asset(self):
        with patch('aicut.media_catalog.probe_asset',return_value={'format':{'duration':'10'}}):
            with self.assertRaises(ValueError):choose_broll(self.catalog,{'query':'腹直肌','role':'detail','at':0,'duration':6,'reason':'x'},20)
    def test_used_asset_not_repeated(self):
        with self.assertRaises(ValueError):choose_broll(self.catalog,{'query':'腹直肌','role':'detail','at':0,'duration':2,'reason':'x'},20,[self.catalog['assets'][0]['id']])
    def test_source_anchor_follows_reordered_timeline(self):
        self.assertEqual(map_anchor(self.clips,'m02',7,10),(12,15))
        self.assertEqual(map_anchor(self.clips[::-1],'m02',7,10),(2,5))
    def test_removed_anchor_rejected(self):
        with self.assertRaises(ValueError):map_anchor(self.clips,'m01',9,11)
    def test_cards_have_motion_and_safe_zone(self):
        cards=compile_cards([{'media_id':'m01','source_start':11,'source_end':14,'title':'准备动作','subtitle':'平躺屈膝','reason':'对应原声'}],self.clips)
        text='\n'.join(ass_lines(cards));self.assertIn('move(',text);self.assertIn('pos(60,145)',text)
    def test_card_overlap_blocked(self):
        card={'media_id':'m01','source_start':11,'source_end':14,'title':'准备动作','reason':'对应原声'}
        with self.assertRaises(ValueError):compile_cards([card,dict(card)],self.clips)
    def test_overlong_card_blocked(self):
        with self.assertRaises(ValueError):compile_cards([{'media_id':'m01','source_start':11,'source_end':14,'title':'字'*30,'reason':'x'}],self.clips)
    def test_completeness_rejects_missing_reason_section(self):
        units=[{'id':'why','title':'为什么','ranges':[{'media_id':'m01','first_segment':1,'last_segment':3}]}]
        plan={'selections':[{'media_id':'m01','first_segment':2,'last_segment':3}]}
        self.assertAlmostEqual(content_coverage(plan,units)[0]['coverage'],2/3)
        with self.assertRaises(ValueError):validate_constraints(plan,{'content_units':units})
    def test_complete_duration_is_explicit_not_force(self):
        sheet={'project':'fixture','cover_text':'标题','cta':'结尾','clips':[{'file':str(self.path),'start':0,'end':220}],'duration_limit':360}
        self.assertEqual(build_draft.quality_gates(sheet),[])
        del sheet['duration_limit'];self.assertTrue(any('总时长' in x for x in build_draft.quality_gates(sheet)))
    def test_broll_stops_and_maintains_voice(self):
        sheet={'clips':[{'file':str(self.path),'start':0,'end':10}],'broll':[{'file':str(self.path),'start':0,'end':2,'at':3,'duration':2}]}
        with patch.object(export_video,'has_audio',return_value=True),patch.object(export_video,'probe_duration',return_value=10):
            command=export_video.build_command(sheet,self.root/'out.mp4',False)
        graph=command[command.index('-filter_complex')+1]
        self.assertIn('repeatlast=0',graph);self.assertNotIn('[1:a]',graph)
    def test_caption_polish_preserves_all_text(self):
        rows=[{'start':0,'end':3,'text':'腹直肌打开的情况'},{'start':3,'end':3.4,'text':'下'}]
        out=polish_captions(rows)
        self.assertEqual(len(out),1);self.assertEqual(out[0]['text'].replace('\n',''),'腹直肌打开的情况下')
        self.assertEqual(out[0]['end'],3.4)
    def test_caption_polish_does_not_cross_long_pause(self):
        self.assertEqual(len(polish_captions([{'start':0,'end':1,'text':'说明'},{'start':3,'end':4,'text':'好'}])),2)
    def test_missing_broll_fails_draft_gate(self):
        sheet={'project':'fixture','cover_text':'标题','cta':'结尾','clips':[{'file':str(self.path),'start':0,'end':10}],
               'broll':[{'file':str(self.root/'missing.mov'),'start':0,'end':2,'at':1,'duration':2}]}
        self.assertTrue(any('B-roll' in x for x in build_draft.quality_gates(sheet)))
    def test_out_of_timeline_card_fails_draft_gate(self):
        sheet={'project':'fixture','cover_text':'标题','cta':'结尾','clips':[{'file':str(self.path),'start':0,'end':10}],
               'kinetic_text':[{'start':9,'end':12,'title':'x'}]}
        self.assertTrue(any('花字' in x for x in build_draft.quality_gates(sheet)))

if __name__=='__main__':unittest.main(verbosity=2)
