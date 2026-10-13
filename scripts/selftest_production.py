"""Isolated regressions: no user media, live editor, LLM or knowledge writes."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT / 'scripts'))
from aicut.production import atomic_json, read_json, project_name, source_version, commit_draft, validate_draft
from aicut.jobs import Job, run_command, Cancelled, mark_interrupted
import preprocess
import build_draft
import pipeline
import auto_edit
import retrospect
from aicut.feedback import load_feedback


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aicut-production-')
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def draft(self, name, marker):
        path = self.root / name
        path.mkdir(parents=True)
        atomic_json(path / 'draft_content.json', {'marker': marker, 'duration': 2000000,
                    'tracks': [{'type': 'video', 'name': 'main', 'segments': [{'target_timerange': {'start': 0, 'duration': 2000000}}]}]})
        return path

    def test_name_traversal(self):
        for name in ('../bad', 'C:\\bad', 'CON', 'NUL.txt', 'bad.', 'a/b', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                project_name(name)
        self.assertEqual(project_name('测试草稿'), '测试草稿')

    def test_atomic_failure_preserves_previous(self):
        path = self.root / 'state.json'
        atomic_json(path, {'version': 1})
        with self.assertRaises(ValueError):
            atomic_json(path, {'version': float('nan')})
        self.assertEqual(read_json(path), {'version': 1})

    def test_asset_identity_and_same_size_update(self):
        a, b = self.root/'a'/'same.mp4', self.root/'b'/'same.mp4'
        a.parent.mkdir(); b.parent.mkdir()
        a.write_bytes(b'123'); b.write_bytes(b'123')
        first, second = source_version(a, {}), source_version(b, {})
        self.assertNotEqual(first['asset_id'], second['asset_id'])
        stamp = a.stat()
        a.write_bytes(b'456'); os.utime(a, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertNotEqual(first['version'], source_version(a, {})['version'])
        self.assertNotEqual(first['version'], source_version(b, {'fps': 30})['version'])

    def test_preprocess_cache_and_missing_output(self):
        src = self.root/'clip.mp4'; src.write_bytes(b'original')
        cfg = {'root': str(self.root), 'render': {'width':1080,'height':1920,'fps':30,
               'preset':'ultrafast','crf':30,'audio_bitrate':'96k'}, 'ffmpeg':'ffmpeg', 'ffprobe':'ffprobe'}
        normalized_path = self.root/'work'/'normalized'/'clip.mp4'
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        def fake(src, name, cfg, root):
            out = normalized_path
            if not out.exists():
                subprocess.run(['ffmpeg','-y','-f','lavfi','-i','color=c=black:s=1080x1920:r=30:d=2',
                                '-an','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(out)],
                               capture_output=True,check=True)
            return {'source': str(src), 'name': name, 'video': str(out), 'audio': None, 'has_audio': False, 'duration': 2}
        with patch.object(preprocess, 'normalize', side_effect=fake) as convert:
            one = preprocess.prepare(src, cfg)
            two = preprocess.prepare(src, cfg)
            self.assertEqual(one, two); self.assertEqual(convert.call_count, 1)
            Path(one['video']).write_bytes(b'corrupt')
            repaired = preprocess.prepare(src, cfg)
            self.assertEqual(repaired['video'], one['video'])
            self.assertEqual(convert.call_count, 2)
            Path(one['video']).unlink()
            three = preprocess.prepare(src, cfg)
            self.assertEqual(one['video'], three['video'])
            src.write_bytes(b'changed')
            four = preprocess.prepare(src, cfg)
            self.assertEqual(three['video'], four['video'])
            self.assertEqual(convert.call_count, 4)
            self.assertTrue(Path(three['video']).exists())

    def test_preprocess_normalize_staging_names_unique_for_same_output(self):
        root = self.root
        sources = [root/'a'/'same.mp4', root/'b'/'same.mp4']
        for src in sources:
            src.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['ffmpeg','-y','-f','lavfi','-i','color=c=black:s=360x640:r=24:d=1',
                            '-an','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(src)],
                           capture_output=True,check=True)
        cfg = {'root': str(root), 'render': {'width':1080,'height':1920,'fps':30,
               'preset':'ultrafast','crf':30,'audio_bitrate':'96k'}, 'ffmpeg':'ffmpeg', 'ffprobe':'ffprobe'}
        staging_names = []
        real_run = preprocess.run
        def record_run(cmd):
            if cmd[-1].endswith('.staging.mp4') or cmd[-1].endswith('.staging.wav'):
                staging_names.append(Path(cmd[-1]).name)
            return real_run(cmd)
        with patch.object(preprocess, 'run', side_effect=record_run):
            first = preprocess.normalize(sources[0], 'same-output', cfg, root)
            second = preprocess.normalize(sources[1], 'same-output', cfg, root)
        self.assertEqual(first['video'], second['video'])
        self.assertEqual(len(staging_names), len(set(staging_names)))
        self.assertEqual(preprocess.probe(Path(first['video']), 'ffprobe')['width'], 1080)

    def test_commit_preserves_backup(self):
        source = self.draft('source', 'new'); target = self.draft('target', 'old')
        result = commit_draft(source, target)
        self.assertEqual(read_json(target/'draft_content.json')['marker'], 'new')
        self.assertEqual(read_json(Path(result['backup'])/'draft_content.json')['marker'], 'old')

    def test_copy_failure_preserves_target(self):
        source = self.draft('source', 'new'); target = self.draft('target', 'old')
        with patch('aicut.production.shutil.copytree', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                commit_draft(source, target)
        self.assertEqual(read_json(target/'draft_content.json')['marker'], 'old')

    def test_commit_rename_failure_restores_target(self):
        source = self.draft('source', 'new'); target = self.draft('target', 'old')
        rename = Path.rename
        def failing(path, dest):
            if path.name.endswith('.staged'):
                raise PermissionError('editor locked')
            return rename(path, dest)
        with patch.object(Path, 'rename', failing), self.assertRaises(PermissionError):
            commit_draft(source, target)
        self.assertEqual(read_json(target/'draft_content.json')['marker'], 'old')
        commit_draft(source, target)
        self.assertEqual(read_json(target/'draft_content.json')['marker'], 'new')

    def test_invalid_draft_cannot_replace(self):
        source = self.draft('source', 'new'); target = self.draft('target', 'old')
        atomic_json(source/'draft_content.json', {'duration': 0})
        with self.assertRaises(ValueError):
            commit_draft(source, target)
        self.assertEqual(read_json(target/'draft_content.json')['marker'], 'old')

    def test_quality_gate_numeric_and_directory(self):
        media = self.root/'clip.mp4'; media.write_bytes(b'fake')
        valid = {'project':'test', 'cover_text':'cover', 'cta':'cta', 'clips':[{'file': str(media), 'start':0, 'end':2}]}
        with patch.object(build_draft, 'media_duration', return_value=2):
            self.assertEqual(build_draft.quality_gates(valid), [])
        for key, value in [('start', -1), ('end', float('nan')), ('speed', 0), ('file', str(self.root))]:
            sheet = copy.deepcopy(valid); sheet['clips'][0][key] = value
            with self.subTest(key=key):
                self.assertTrue(build_draft.quality_gates(sheet))

    def test_quality_gate_rejects_source_ranges_past_video_duration(self):
        media = self.root/'short.mp4'; media.write_bytes(b'fake')
        sheet = {'project':'test','cover_text':'cover','cta':'cta',
                 'clips':[{'file':str(media),'start':0,'end':2}],
                 'broll':[{'file':str(media),'start':0,'end':1.5,'at':0,'duration':1}]}
        with patch.object(build_draft, 'media_duration', return_value=1.0) as probe:
            problems = build_draft.quality_gates(sheet)
        self.assertTrue(any('超出源素材时长' in issue for issue in problems))
        self.assertEqual(probe.call_count, 1)

    def test_quality_gate_allows_still_image_timeline_duration(self):
        image = self.root/'still.jpg'; image.write_bytes(b'fixture image')
        sheet = {'project':'test','cover_text':'cover','cta':'cta',
                 'clips':[{'file':str(image),'start':0,'end':3}]}
        with patch.object(build_draft, 'media_duration', side_effect=AssertionError('image is not probed')):
            self.assertEqual(build_draft.quality_gates(sheet), [])

    def test_nonzero_exit_is_failure(self):
        with self.assertRaises(subprocess.CalledProcessError):
            run_command([sys.executable, '-c', 'raise SystemExit(7)'], cwd=self.root)

    def test_timeout_kills_process(self):
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            run_command([sys.executable, '-c', 'import time; time.sleep(60)'], cwd=self.root, timeout=.3)
        self.assertLess(time.monotonic() - start, 10)

    def test_cancel_kills_process(self):
        start = time.monotonic()
        with self.assertRaises(Cancelled):
            run_command([sys.executable, '-c', 'import time; time.sleep(60)'], cwd=self.root,
                        cancelled=lambda: time.monotonic()-start > .3)
        self.assertLess(time.monotonic() - start, 10)

    def test_job_records_failed_stage(self):
        job = Job(self.root, 'failed-test')
        with self.assertRaises(ValueError):
            job.stage('quality', lambda: pipeline._require(False, 'bad media'))
        job.finish('failed', 'bad media')
        data = read_json(job.path)
        self.assertEqual(data['stages'][0]['state'], 'failed')
        self.assertEqual(data['state'], 'failed')

    def test_transcript_out_of_bounds(self):
        path = self.root/'transcript.json'
        atomic_json(path, {'segments':[{'start':0, 'end':20, 'text':'bad'}]})
        with self.assertRaises(ValueError):
            pipeline.validate_transcript(path, 2)

    def test_transcript_rejects_invalid_text_and_nonmonotonic_time(self):
        path = self.root/'transcript.json'
        for segments in (
            [{'start':0, 'end':1, 'text':'  '}],
            [{'start':1, 'end':2, 'text':'later'}, {'start':0, 'end':.5, 'text':'earlier'}],
            [{'start':0, 'end':float('nan'), 'text':'bad'}],
        ):
            path.write_text(json.dumps({'segments':segments}, allow_nan=True), encoding='utf-8')
            with self.subTest(segments=segments), self.assertRaises(ValueError):
                pipeline.validate_transcript(path, 3)

    def test_transcript_rejects_duration_mismatch(self):
        path = self.root/'transcript.json'
        atomic_json(path, {'duration':30, 'segments':[{'start':0, 'end':1, 'text':'hello'}]})
        with self.assertRaises(ValueError):
            pipeline.validate_transcript(path, 2)

    def test_asr_cache_binds_audio_bytes_and_settings(self):
        audio = self.root/'audio.wav'; audio.write_bytes(b'original audio')
        transcript = self.root/'transcript.json'
        cfg = {'model':'small','language':'zh','beam_size':5,'vad':True,
               'device':'cpu','compute_type':'int8'}
        atomic_json(transcript, {'_asr':{'model':'small','language':'zh','beam_size':5,'vad':True,
            'requested_device':'cpu','requested_compute_type':'int8','gpu_compute_type':'float16',
            'word_timestamps':False,'vocabulary':'',
            'source_sha256':hashlib.sha256(audio.read_bytes()).hexdigest()}})
        self.assertTrue(pipeline.transcript_cache_matches(transcript, audio, cfg))
        audio.write_bytes(b'changed audio')
        self.assertFalse(pipeline.transcript_cache_matches(transcript, audio, cfg))

    def pipeline_case(self, *, degraded=False, review_ok=True, copilot=False, fail_transcribe=False,
                      empty_transcript=False, review_breaks_assets=False):
        source = self.root/'clip.mp4'; source.write_bytes(b'fixture')
        args = SimpleNamespace(video=str(source), name='film', transcript=None, semantic=False,
                               skip_transcribe=False, analyze_only=False, no_build=False,
                               copilot=copilot, export_mp4=False, publish=True, no_publish=False,
                               vocabulary='')
        job = Job(self.root, 'pipeline-test')
        published = []
        def command(cmd, root):
            script = Path(cmd[1]).name
            if script == 'preprocess.py':
                if fail_transcribe or empty_transcript:
                    (self.root/'audio.wav').write_bytes(b'audio')
                atomic_json(self.root/'work/manifest.json', {'items':[{'name':'clip', 'label':'clip',
                    'video':str(source), 'source':str(source), 'duration':2,
                    'has_audio':fail_transcribe or empty_transcript,
                    'audio':str(self.root/'audio.wav') if (fail_transcribe or empty_transcript) else None}]})
            elif script == 'scan_every_frame.py':
                if empty_transcript:
                    self.fail('frame scan must not start after empty ASR with audio')
                atomic_json(Path(cmd[cmd.index('--status-out')+1]),
                            {'state':'complete','complete':True,'expected_frames':2,'observed_frames':2})
            elif script == 'transcribe.py':
                if fail_transcribe:
                    raise subprocess.CalledProcessError(1, cmd)
                transcript = self.root/'02-transcript'/'clip.json'
                transcript.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(transcript, {'duration':2,'segments':[] if empty_transcript else [{'start':0,'end':1,'text':'hello'}],
                    '_asr':{'model':'small','language':'zh','beam_size':5,'vad':True,
                            'requested_device':'cpu','requested_compute_type':'int8','gpu_compute_type':'float16',
                            'word_timestamps':False,'vocabulary':'',
                            'source_sha256':hashlib.sha256((self.root/'audio.wav').read_bytes()).hexdigest()}})
            elif script == 'auto_edit.py':
                intent = self.root/'intent.json'
                atomic_json(intent, {'_meta':{'degraded':degraded}})
                atomic_json(self.root/'03-scripts/film.sheet.json', {'project':'film','cover_text':'test','cta':'test',
                            'clips':[{'file':str(source), 'start':0,'end':2,'effect':'blur' if copilot else None}], 'intent_path':str(intent)})
            elif script == 'build_draft.py':
                built = self.draft('06-drafts/film', 'original')
                # Runtime auditing now checks the actual trim/speed/volume and
                # material references as well as decoration tracks.
                doc = read_json(built/'draft_content.json')
                doc['tracks'][0]['segments'][0].update({
                    'source_timerange': {'start': 0, 'duration': 2000000},
                    'speed': 1, 'volume': 1, 'material_id': 'source-video'})
                doc['materials'] = {'videos': [{'id': 'source-video', 'path': str(source)}]}
                atomic_json(built/'draft_content.json', doc)
                if copilot:
                    doc = read_json(built/'draft_content.json')
                    doc['tracks'].append({'type':'effect','name':'effect','segments':[{'target_timerange':{'start':0,'duration':2000000}}]})
                    atomic_json(built/'draft_content.json', doc)
                atomic_json(self.root/'06-drafts/film/aicut-runtime-assets.json',
                            {'schema_version':'runtime-decoration-1','status':'complete','checks':{}})
            elif script == '-c':
                path = self.root/'06-drafts/film/draft_content.json'
                doc=read_json(path); doc['marker']='reviewed'; atomic_json(path, doc)
                if review_breaks_assets:
                    doc['tracks']=[t for t in doc['tracks'] if t.get('name') != 'effect']
                    atomic_json(path, doc)
                atomic_json(job.directory/'review.json', {'ok':True,'review':{'ready_to_ship':review_ok}})
            else:
                self.fail('unexpected child: '+str(cmd))
        def publish(path, cfg):
            published.append(read_json(path/'draft_content.json')['marker'])
        with patch.object(pipeline, 'ROOT', self.root), patch.object(preprocess, 'load_config', return_value={'draft_root':str(self.root/'editor')}), \
             patch.object(build_draft, 'media_duration', return_value=2), \
             patch.object(job, 'command', side_effect=command), patch.object(build_draft, 'publish', side_effect=publish):
            try:
                state = pipeline.execute(args, [], job)
            except subprocess.CalledProcessError:
                state = 'failed'
            except ValueError:
                state = 'failed'
            job.finish(state)
        return state, published, job.data

    def test_pipeline_publishes_reviewed_version(self):
        state, published, data = self.pipeline_case(copilot=True)
        self.assertEqual(state, 'completed')
        self.assertEqual(published, ['reviewed'])
        self.assertEqual(data['stages'][-1]['name'], '复制复审后草稿到剪映')

    def test_pipeline_rechecks_runtime_assets_after_review(self):
        state, published, data = self.pipeline_case(copilot=True, review_breaks_assets=True)
        self.assertEqual(state, 'needs_review')
        self.assertEqual(published, [])
        self.assertIn('复审后的草稿未通过花字/特效回读校验；草稿已保留', data['warnings'])

    def test_runtime_transition_report_counts_segment_references(self):
        draft = self.root/'transition-draft'
        draft.mkdir()
        source = self.root/'transition-source.mp4'
        content = {'tracks':[{'name':'main','segments':[{'extra_material_refs':[],
                   'target_timerange':{'start':0,'duration':2000000},
                   'source_timerange':{'start':0,'duration':2000000},
                   'speed':1,'volume':1,'material_id':'source-video'}]}],
                   'materials':{'transitions':[{'id':'transition-1'}],
                                'videos':[{'id':'source-video','path':str(source)}]}}
        atomic_json(draft/'draft_content.json', content)
        sheet = {'clips':[{'file':str(source),'start':0,'end':2,'转场调用名':'fade'}]}
        report = build_draft.runtime_asset_report(sheet, draft)
        self.assertEqual(report['checks']['transitions']['applied'], 0)
        self.assertEqual(report['status'], 'needs_review')

        content['tracks'][0]['segments'][0]['extra_material_refs']=['transition-1']
        atomic_json(draft/'draft_content.json', content)
        report = build_draft.runtime_asset_report(sheet, draft)
        self.assertEqual(report['checks']['transitions']['applied'], 1)
        self.assertEqual(report['status'], 'complete')

    def test_pipeline_blocks_rejected_review(self):
        state, published, _ = self.pipeline_case(copilot=True, review_ok=False)
        self.assertEqual(state, 'needs_review')
        self.assertEqual(published, [])

    def test_pipeline_blocks_degraded_model(self):
        state, published, _ = self.pipeline_case(degraded=True)
        self.assertEqual(state, 'needs_review')
        self.assertEqual(published, [])

    def test_pipeline_transcribe_failure_stops_plan(self):
        state, published, data = self.pipeline_case(fail_transcribe=True)
        self.assertEqual(state, 'failed')
        self.assertEqual(published, [])
        self.assertEqual(len(data['stages']), 2)

    def test_pipeline_empty_asr_with_audio_stops_automatic_edit(self):
        state, published, data = self.pipeline_case(empty_transcript=True)
        self.assertEqual(state, 'failed')
        self.assertEqual(published, [])
        self.assertEqual(len(data['stages']), 2)
        self.assertIn('逐字稿为空', data['stages'][-1]['error'])

    def test_feedback_binds_exact_work(self):
        source=self.root/'metrics.csv'
        source.write_text('标题,播放,完播率\nfirst,100,20%\nsecond,200,30%\n', encoding='utf-8-sig')
        data=load_feedback(source, 'second')
        self.assertEqual(data['row']['播放'], '200')
        self.assertEqual(len(data['sha256']), 64)
        with self.assertRaises(ValueError):
            load_feedback(source, 'unknown')

    def test_feedback_rejects_ambiguous_work(self):
        source=self.root/'metrics.csv'
        source.write_text('标题,播放\nsame,100\nsame,200\n', encoding='utf-8-sig')
        with self.assertRaises(ValueError):
            load_feedback(source, 'same')

    def test_stale_understanding_regenerates_in_same_call(self):
        video=self.root/'clip.mp4'; video.write_bytes(b'video')
        cache=self.root/'work/video-understanding-clip.json'
        atomic_json(cache, {'素材指纹':{'大小':0,'修改时间':0}, '文本理解':{'old':True}})
        calls=[]
        def run(cmd, **kwargs):
            calls.append(cmd)
            doc={'素材指纹':{'大小':video.stat().st_size,'修改时间':int(video.stat().st_mtime)},
                 '文本理解':{'fresh':True}, '识别方案版本':2, '视觉状态':'失败'}
            if '--out' in cmd:
                atomic_json(cmd[cmd.index('--out')+1], doc)
            else:
                atomic_json(cache, doc)
            return subprocess.CompletedProcess(cmd, 0)
        with patch.object(auto_edit, 'ROOT', self.root), patch.object(auto_edit.subprocess, 'run', side_effect=run):
            data=auto_edit.load_understanding(video, True, None)
        self.assertTrue(data['文本理解']['fresh'])
        self.assertEqual(len(calls), 2)
        self.assertTrue(list(cache.parent.glob('*.stale-*')))

    def test_explicit_stale_understanding_is_not_deleted(self):
        video=self.root/'clip.mp4'; video.write_bytes(b'video')
        cache=self.root/'explicit.json'
        atomic_json(cache, {'素材指纹':{'大小':0,'修改时间':0}})
        with self.assertRaises(ValueError):
            auto_edit.load_understanding(video, True, str(cache))
        self.assertTrue(cache.is_file())

    def test_interrupted_job_records_state_without_overwriting_success(self):
        job=Job(self.root, 'interrupted')
        job.data['stages']=[{'name':'render', 'state':'running'}]; job.save()
        mark_interrupted(self.root, 'interrupted', 'cancelled', 'backend stopped')
        self.assertEqual(read_json(job.path)['stages'][0]['state'], 'cancelled')
        job.finish('completed')
        mark_interrupted(self.root, 'interrupted', 'failed', 'late error')
        self.assertEqual(read_json(job.path)['state'], 'completed')

    @unittest.skipUnless(os.name=='nt', 'Windows process tree test')
    def test_timeout_kills_descendant(self):
        import ctypes
        lines=[]
        code='import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); print(p.pid,flush=True); time.sleep(60)'
        with self.assertRaises(TimeoutError):
            run_command([sys.executable,'-c',code], cwd=self.root, timeout=1, on_line=lines.append)
        self.assertTrue(lines)
        kernel=ctypes.windll.kernel32
        kernel.OpenProcess.restype=ctypes.c_void_p
        handle=kernel.OpenProcess(0x1000,False,int(lines[0]))
        if handle:
            status=ctypes.c_ulong()
            kernel.GetExitCodeProcess(ctypes.c_void_p(handle),ctypes.byref(status))
            kernel.CloseHandle(ctypes.c_void_p(handle))
            self.assertNotEqual(status.value,259,'child still running')

    def test_retrospect_consumes_metrics_and_preserves_evidence(self):
        sheet=self.root/'sheet.json'; atomic_json(sheet,{'project':'film','clips':[]})
        metrics=self.root/'metrics.csv'; metrics.write_text('标题,播放\nfilm,321\n',encoding='utf-8-sig')
        argv=['retrospect','--sheet',str(sheet),'--data',str(metrics),'--note','test observation']
        with patch.object(retrospect,'ROOT',self.root), patch.object(sys,'argv',argv), \
             patch.object(retrospect,'distill_lessons',return_value='grounded observation') as distill, \
             patch.object(retrospect.cl,'add_case',return_value='case:film') as add_case, \
             patch.object(retrospect.ms,'upsert'), patch.object(retrospect.ms,'count',return_value=1):
            self.assertEqual(retrospect.main(),0)
        self.assertIn('321',distill.call_args.args[2])
        self.assertEqual(add_case.call_args.kwargs['post_data']['metrics']['row']['播放'],'321')
        self.assertEqual(len(list((self.root/'08-data/feedback').glob('*.json'))),1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
