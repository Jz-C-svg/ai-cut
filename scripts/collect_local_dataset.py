"""Read-only local dataset collection. No rendering, training or uploads."""
import argparse, concurrent.futures, hashlib, json, math, subprocess, sys, zipfile
from pathlib import Path
from datetime import datetime
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
from aicut.media_catalog import index_library

def save(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_suffix(p.suffix+'.tmp');temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(p)

def run(args,**kw):
    return subprocess.run(args,capture_output=True,check=True,timeout=180,**kw)

def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def audio_metrics(p,channels):
    # Analysis at 16 kHz; native channels retained. No media file is written.
    data=run(['ffmpeg','-v','error','-i',str(p),'-map','0:a:0','-vn','-ar','16000','-f','f32le','-']).stdout
    x=np.frombuffer(data,dtype='<f4').reshape(-1,channels).astype(np.float64)
    if not len(x):return {'status':'empty'}
    power=np.mean(x*x,axis=0);db=lambda a:float(10*np.log10(max(float(a),1e-15)))
    result={'analysis_sample_rate':16000,'channels':channels,'rms_dbfs':[db(v) for v in power],
            'sample_peak_dbfs':[float(20*np.log10(max(v,1e-15))) for v in np.max(np.abs(x),axis=0)],
            'note':'Full audio decoded at 16 kHz; sample peak is not true peak; thresholds are review heuristics.'}
    if channels==2:
        l,r=x.T;mid=(l+r)/2;side=(l-r)/2
        result.update(balance_db=db(power[0])-db(power[1]),correlation=float(np.corrcoef(l,r)[0,1]) if min(power)>1e-12 else None,
                      mono_fold_loss_db=db(np.mean(mid*mid))-db(np.mean(power)),side_to_mid_db=db(np.mean(side*side))-db(np.mean(mid*mid)))
    return result

def collect(p,source,out):
    st=p.stat();sha=digest(p);cache=out/'records'/(hashlib.sha256(str(p).encode()).hexdigest()[:20]+'.json')
    if cache.exists():
        old=json.loads(cache.read_text(encoding='utf-8'))
        if old.get('sha256')==sha and old.get('status')=='ok':return old
    row={'file':str(p),'relative':p.relative_to(source).as_posix(),'sha256':sha,'bytes':st.st_size,'mtime_ns':st.st_mtime_ns,
         'review_status':'unreviewed','issues':[],'status':'ok'}
    ext=p.suffix.lower()
    try:
        if ext in {'.mp4','.mov','.mkv','.wav','.mp3','.m4a'}:
            row['kind']='video' if ext in {'.mp4','.mov','.mkv'} else 'audio'
            meta=json.loads(run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(p)]).stdout)
            row['metadata']=meta;duration=float(meta['format'].get('duration',0));row['duration']=duration
            aud=[s for s in meta['streams'] if s['codec_type']=='audio']
            if aud:
                a=audio_metrics(p,int(aud[0]['channels']));row['audio']=a
                if abs(a.get('balance_db',0))>3:row['issues'].append('channel_balance_gt_3db_review')
                if a.get('correlation') is not None and a['correlation']<0:row['issues'].append('negative_channel_correlation_review')
                if a.get('mono_fold_loss_db',0)<-6:row['issues'].append('mono_fold_loss_gt_6db_review')
                if max(a.get('rms_dbfs',[-150]))<-45:row['issues'].append('low_audio_level_review')
            else:row['issues'].append('no_audio')
            if row['kind']=='video':
                row['frames']=[]
                for i,fraction in enumerate((.1,.5,.9)):
                    at=duration*fraction;target=out/'frames'/f'{sha[:20]}-{i}.jpg';target.parent.mkdir(exist_ok=True)
                    if not target.exists():run(['ffmpeg','-v','error','-ss',str(at),'-i',str(p),'-frames:v','1','-vf','scale=360:-2','-y',str(target)])
                    with Image.open(target) as im:im.verify()
                    row['frames'].append({'time':at,'file':str(target),'visual_reviewed':False})
                row['decode_scope']='three video frames and full audio; not full video decode'
        elif ext in {'.jpg','.jpeg','.png','.webp'}:
            row['kind']='image'
            with Image.open(p) as im:row['image']={'width':im.width,'height':im.height,'mode':im.mode};im.verify()
        elif ext in {'.zip','.rar'}:
            row['kind']='archive';row['issues'].append('archive_not_extracted_or_media_indexed')
            if ext=='.zip':
                with zipfile.ZipFile(p) as z:row['archive_members']=[{'name':i.filename,'bytes':i.file_size} for i in z.infolist()]
        else:row['kind']='other';row['issues'].append('unsupported_format')
        after=p.stat()
        if (st.st_size,st.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('source_changed_during_collection')
    except Exception as e:row['status']='error';row['error']=str(e)[:1500]
    save(cache,row);return row

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--out',required=True);ap.add_argument('--asr',action='store_true');ap.add_argument('--device',choices=['cpu','cuda'],default='cpu');ap.add_argument('--resume-asr',action='store_true');args=ap.parse_args()
    source=Path(args.source).resolve();out=Path(args.out).resolve()
    if not source.is_dir() or out.is_relative_to(source):raise ValueError('Source must exist and output must be outside source')
    out.mkdir(parents=True,exist_ok=True);files=sorted(p for p in source.rglob('*') if p.is_file() and p.resolve().is_relative_to(source))
    rows=[]
    if args.resume_asr:
        rows=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        if {str(p) for p in files}!={r['file'] for r in rows}:raise ValueError('Source inventory changed; repeat collection')
        for row in rows:
            st=Path(row['file']).stat()
            if (st.st_size,st.st_mtime_ns)!=(row['bytes'],row['mtime_ns']):raise ValueError('Source changed; repeat collection')
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for row in pool.map(lambda p:collect(p,source,out),files):
                rows.append(row);save(out/'manifest.json',rows)
                print(f'collect {len(rows)}/{len(files)} {row["status"]} {row["relative"]}',flush=True)
    seen={}
    for row in rows:
        row['duplicate_of']=seen.get(row['sha256']);seen.setdefault(row['sha256'],row['file'])
        row['split']='unassigned_pending_group_and_content_review'
    save(out/'manifest.json',rows)
    if args.asr:
        from faster_whisper import WhisperModel
        model=None
        for i,row in enumerate(rows):
            if row['status']!='ok' or row.get('kind') not in {'video','audio'} or not row.get('audio'):continue
            target=out/'transcripts'/(row['sha256']+'.json')
            if not target.exists():
                if model is None:model=WhisperModel('small',device=args.device,compute_type='int8_float16' if args.device=='cuda' else 'int8',cpu_threads=8,download_root=str(ROOT/'models'),local_files_only=True)
                try:
                    segs,info=model.transcribe(row['file'],language='zh',beam_size=5,vad_filter=True,word_timestamps=True,condition_on_previous_text=False)
                    segments=[{'start':s.start,'end':s.end,'text':s.text,'avg_logprob':s.avg_logprob,'words':[{'word':w.word,'start':w.start,'end':w.end,'probability':w.probability} for w in s.words or []]} for s in segs]
                    save(target,{'source_sha256':row['sha256'],'model':'faster-whisper/small','device':args.device,'language':'zh','review_status':'automatic_unreviewed','segments':segments})
                except Exception as e:row['issues'].append('asr_failed');row['asr_error']=str(e);continue
            row['transcript']=str(target);print(f'asr {i+1}/{len(rows)} {row["relative"]}',flush=True)
            save(out/'manifest.json',rows)
    catalog=index_library(source);by_file={r['file']:r for r in rows}
    for asset in catalog['assets']:
        r=by_file[asset['file']];asset['dataset_sha256']=r['sha256'];asset['dataset_issues']=r['issues']
        if r.get('transcript'):
            t=json.loads(Path(r['transcript']).read_text(encoding='utf-8'));asset['description']=' '.join(s['text'] for s in t['segments']);asset['search_text']+=' '+asset['description'];asset['description_basis']='automatic_asr_unreviewed';asset['transcript']=r['transcript']
    save(out/'asset-catalog.json',catalog)
    summary={'source':str(source),'created_at':datetime.now().isoformat(),'files':len(rows),'unique_hashes':len(seen),'duplicates':len(rows)-len(seen),'bytes':sum(r['bytes'] for r in rows),'duration_seconds':sum(r.get('duration',0) for r in rows),'kinds':{k:sum(r.get('kind')==k for r in rows) for k in sorted({r.get('kind','unknown') for r in rows})},'errors':sum(r['status']=='error' for r in rows),'transcribed_files':sum(bool(r.get('transcript')) for r in rows),'flagged_files':sum(bool(r['issues']) for r in rows),'originals_modified':False,'training_performed':False,'automatic_labels_reviewed':False}
    save(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
