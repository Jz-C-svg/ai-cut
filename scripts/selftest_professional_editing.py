import hashlib,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
sys.path.insert(0,str(ROOT/'scripts'))
from aicut.professional_editing import compile_plan, speech_windows, word_captions, read_records, validate_constraints
from aicut.learning_context import retrieve, catalog
from analyze_folder import select_folder
from render_folder_plan import corrected_words

class ProfessionalTests(unittest.TestCase):
    def setUp(self):
        self.media=[{'id':'m01','file':'test.mov','duration':10,'segments':[
            {'start':1,'end':3,'text':'不是所有人都需要'},
            {'start':4,'end':6,'text':'如果有不适请咨询专业人员'},
            {'start':7,'end':9,'text':'这是完整结尾'}]}]
        self.plan={'selections':[{'media_id':'m01','first_segment':1,'last_segment':2,'reason':'保留条件','evidence_ids':['pro-test']}]}
    def test_full_sentences_preserve_negation(self):
        p=compile_plan(self.plan,self.media,['pro-test'],padding=.08)
        self.assertEqual(p['clips'][0]['start'],.92)
        self.assertIn('不是',p['decisions'][0]['source_text']);self.assertIn('如果',p['decisions'][0]['source_text'])
    def test_unknown_media(self):
        self.plan['selections'][0]['media_id']='C:/secret.mov'
        with self.assertRaises(ValueError):compile_plan(self.plan,self.media,['pro-test'])
    def test_invented_segment(self):
        self.plan['selections'][0]['last_segment']=99
        with self.assertRaises(ValueError):compile_plan(self.plan,self.media,['pro-test'])
    def test_unknown_citation(self):
        with self.assertRaises(ValueError):compile_plan(self.plan,self.media,[])
    def test_duplicate(self):
        self.plan['selections']*=2
        with self.assertRaises(ValueError):compile_plan(self.plan,self.media,['pro-test'])
    def test_duration_bound(self):
        with self.assertRaises(ValueError):compile_plan(self.plan,self.media,['pro-test'],max_duration=2)
    def test_excluded_source_not_executable(self):
        with self.assertRaises(ValueError):validate_constraints(self.plan,{'excluded_ranges':[{'media_id':'m01','first_segment':2,'last_segment':3}]})
    def test_demonstration_requires_setup(self):
        self.plan['selections'][0]['first_segment']=2
        with self.assertRaises(ValueError):validate_constraints(self.plan,{'required_ranges':[{'media_id':'m01','first_segment':1,'last_segment':1,'reason':'准备姿势'}]})
    def test_random_folder_reproducibility(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('a','b','c'):
                (root/name).mkdir();(root/name/'take.MOV').write_bytes(b'fixture')
            self.assertEqual(select_folder(root,123),select_folder(root,123))
            self.assertEqual(select_folder(root,123)[2],3)
    def test_term_correction_keeps_timing_and_whole_term(self):
        words=[{'word':'腐脂肌','start':1,'end':2},{'word':'并不是','start':2.1,'end':3}];audit=[]
        result=corrected_words(words,[{'old':'腐脂肌','new':'腹直肌'}],audit)
        self.assertEqual(result[0],{'word':'腹直肌','start':1,'end':2})
        self.assertEqual(result[1]['word'],'并不是');self.assertEqual(len(audit),1)
    def test_protected_silent_demonstration(self):
        self.assertEqual(speech_windows([(1,2)],6,0,protected=[(3,5)]),[[1,2],[3,5]])
    def test_caption_words_not_uniform_interpolation(self):
        words=[{'word':'如果','start':1,'end':1.5},{'word':'不适','start':1.6,'end':2.0},{'word':'停止。','start':2.1,'end':3.0}]
        subs=word_captions(words,max_chars=4)
        self.assertEqual(subs[1]['start'],2.1);self.assertEqual(''.join(x['text'] for x in subs),'如果不适停止。')
    def test_test_split_never_retrieved(self):
        warnings=[]
        records=list(read_records(json.dumps({'tier':'professional_reference','split':'test'}),'x',set(),warnings))
        self.assertEqual(records,[]);self.assertTrue(warnings)
    def test_real_data_integrity_and_holdout(self):
        base=ROOT/'08-data/professional-datasets/20260923'
        manifest=json.loads((base/'manifest.json').read_text(encoding='utf-8'))
        for ds in manifest['datasets']:
            for f in ds['files']:
                self.assertEqual(hashlib.sha256((base/f['path']).read_bytes()).hexdigest(),f['sha256'])
        for name,key in [('ava','video_id'),('avecompass','group')]:
            rows=[json.loads(s) for s in (base/name/'annotations.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertFalse({r[key] for r in rows if r['split']=='train'} & {r[key] for r in rows if r['split']=='test'})
    def test_live_retrieval_contains_dataset_rules(self):
        result=retrieve('多素材口播剪辑 完整句子 切点 人声 噪声 画面 任务范围',root=ROOT,budget=12500)
        docs=[x for x in result['evidence'] if x['category']=='professional_dataset']
        self.assertGreaterEqual(len(docs),2)
        self.assertTrue(all(d['split']=='train' for d in docs))
    def test_simulation_heldout_improvement(self):
        report=json.loads((ROOT/'08-data/professional-datasets/20260923/simulation-report.json').read_text(encoding='utf-8'))
        self.assertGreater(report['test_optimized']['speech_preserved_fraction'],report['test_baseline']['speech_preserved_fraction'])
        self.assertGreater(report['test_optimized']['removed_timeline_fraction'],.1)

if __name__=='__main__':unittest.main(verbosity=2)
