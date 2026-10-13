"""Read-only folder selection and cached local word-timed ASR for plan_folder.py."""
import argparse,hashlib,json,random,secrets,subprocess
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EXTENSIONS={'.mp4','.mov','.mkv','.avi','.m4v','.webm'}

def select_folder(root,seed):
    groups=defaultdict(list)
    for p in Path(root).rglob('*'):
        if p.is_file() and p.suffix.lower() in EXTENSIONS:groups[p.parent].append(p)
    if not groups:raise ValueError('No folders containing video files')
    folders=sorted(groups,key=lambda p:str(p).casefold())
    folder=random.Random(seed).choice(folders)
    return folder,sorted(groups[folder]),len(folders)

def main():
    ap=argparse.ArgumentParser();group=ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--folder');group.add_argument('--random-root');ap.add_argument('--seed',type=int)
    ap.add_argument('--out',required=True);ap.add_argument('--vocabulary',default='')
    ap.add_argument('--model',default='small');args=ap.parse_args()
    seed=args.seed if args.seed is not None else secrets.randbits(64)
    if args.random_root:folder,files,count=select_folder(args.random_root,seed)
    else:
        folder=Path(args.folder);files=sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS);count=1
    if not files:raise ValueError('No videos in selected folder')
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    if out.resolve()==folder.resolve():raise ValueError('Use an output directory separate from source folder')
    def save(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    save('selection.json',{'seed':seed,'eligible_folders':count,'selected_folder':str(folder),'files':[str(p) for p in files]})
    cfg=json.loads((ROOT/'config/aicut.json').read_text(encoding='utf-8'))
    from faster_whisper import WhisperModel
    model=None;results=[]
    prompt=('专业术语：'+args.vocabulary[:500]+'。只转写实际说话，不添加词句。') if args.vocabulary else None
    for i,path in enumerate(files,1):
        with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        key=hashlib.sha256(json.dumps([str(path.resolve()),digest,args.model,prompt,5,'zh'],ensure_ascii=False).encode()).hexdigest()
        cached=out/(key+'.transcript.json')
        if cached.exists():row=json.loads(cached.read_text(encoding='utf-8'))
        else:
            info=json.loads(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(path)],capture_output=True,text=True,check=True).stdout)
            segments=[]
            if any(s['codec_type']=='audio' for s in info['streams']):
                if model is None:model=WhisperModel(args.model,device='cpu',compute_type='int8',cpu_threads=8,download_root=cfg['asr']['model_dir'],local_files_only=True)
                rows,_=model.transcribe(str(path),language='zh',beam_size=5,vad_filter=True,word_timestamps=True,condition_on_previous_text=False,initial_prompt=prompt)
                segments=[{'start':s.start,'end':s.end,'text':s.text,'avg_logprob':s.avg_logprob,
                           'words':[{'word':w.word,'start':w.start,'end':w.end,'probability':w.probability} for w in s.words or []]} for s in rows]
            row={'file':str(path.resolve()),'duration':float(info['format']['duration']),'source_sha256':digest,
                 'model':args.model,'vocabulary_hint':args.vocabulary,'segments':segments}
            cached.write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
        results.append(row);save('transcripts.json',results)
        print(f'{i}/{len(files)} {path.name}: {len(row["segments"])} segments',flush=True)
    print('Next input: '+str(out/'transcripts.json'))
if __name__=='__main__':main()
