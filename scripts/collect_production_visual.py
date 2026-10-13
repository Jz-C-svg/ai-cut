"""Collect rendered production evidence and timestamped frames; never render a video."""
import argparse,concurrent.futures,hashlib,json,re,subprocess,sys,statistics
from pathlib import Path
import numpy as np
import cv2
from PIL import Image,ImageDraw,ImageFont
from collect_local_dataset import save
ROOT=Path(__file__).resolve().parents[1]
VERSION='production-visual-v1'

def parse_frames(text):
    frames=[]
    for line in text.splitlines():
        m=re.search(r'frame:(\d+).*?pts_time:([\d.]+)',line)
        if m:frames.append({'number':int(m[1])+1,'time':float(m[2]),'scene_score':0.0})
        m=re.search(r'lavfi.scene_score=([\d.]+)',line)
        if m and frames:frames[-1]['scene_score']=float(m[1])
    return frames

def union_duration(intervals,total):
    merged=[]
    for start,end in sorted((max(0,s),min(total,e)) for s,e in intervals if e>s):
        if end<=start:continue
        if merged and start<=merged[-1][1]:merged[-1][1]=max(end,merged[-1][1])
        else:merged.append([start,end])
    return sum(e-s for s,e in merged)

def build_timeline(frames,duration,threshold):
    cuts=[]
    for f in frames:
        t=f['time']
        if .05<t<duration-.05 and f['scene_score']>threshold:
            if not cuts or t-cuts[-1]>.25:cuts.append(t)
    bounds=[0]+cuts+[duration]
    return cuts,[{'start':s,'end':e,'duration':e-s,'status':'candidate_shot_not_manually_verified'} for s,e in zip(bounds,bounds[1:])]

def contact(frames,target,title):
    chosen=[frames[i] for i in sorted(set(np.linspace(0,len(frames)-1,min(12,len(frames))).astype(int)))]
    width,height=216,404;sheet=Image.new('RGB',(width*4,height*3+48),'#20242a');draw=ImageDraw.Draw(sheet)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',14)
    draw.text((10,8),title[:65],font=font,fill='white')
    for i,row in enumerate(chosen):
        im=Image.open(row['file']).convert('RGB');im.thumbnail((width,height-30));x=i%4*width;y=48+i//4*height
        sheet.paste(im,(x+(width-im.width)//2,y));draw.text((x+5,y+height-25),f"{i+1} | {row['time']:.2f}s",font=font,fill='white')
    sheet.save(target,quality=88);return [{'index':i+1,**r} for i,r in enumerate(chosen)]

def collect(row,out,threshold,gap,hwaccel=None):
    path=Path(row['file']);st=path.stat()
    if (st.st_size,st.st_mtime_ns)!=(row['bytes'],row['mtime_ns']):raise ValueError('Source changed: '+str(path))
    dest=out/'videos'/row['sha256'];dest.mkdir(parents=True,exist_ok=True);result_path=dest/'production.json'
    if result_path.exists():
        old=json.loads(result_path.read_text(encoding='utf-8'))
        if old.get('version')==VERSION and old.get('threshold')==threshold and old.get('max_frame_gap')==gap and all(Path(f['file']).exists() for f in old.get('frames',[])):return old
    vf=f"setpts=PTS-STARTPTS,scale=540:-2,select='gt(scene,{threshold})+isnan(prev_selected_t)+gte(t-prev_selected_t,{gap})',metadata=print:file=-"
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-nostats','-threads','2','-i',str(path),'-map','0:v:0','-an','-vf',vf,'-filter_threads','2','-fps_mode','vfr','-q:v','3','-threads','2','-y',str(dest/'frame-%06d.jpg')]
    if hwaccel:cmd[cmd.index('-i'):cmd.index('-i')]=['-hwaccel',hwaccel]
    proc=subprocess.run(cmd,capture_output=True,timeout=1200)
    decoder=hwaccel or 'software'
    if proc.returncode and hwaccel:
        (dest/'hardware-decode-error.log').write_bytes(proc.stderr)
        position=cmd.index('-hwaccel');del cmd[position:position+2]
        proc=subprocess.run(cmd,capture_output=True,timeout=1200);decoder='software_fallback'
    if proc.returncode:raise RuntimeError(proc.stderr.decode('utf-8',errors='replace')[-1500:])
    (dest/'frame-metadata.txt').write_bytes(proc.stdout)
    frames=parse_frames(proc.stdout.decode('utf-8',errors='replace'))
    if not frames:raise ValueError('No frames extracted')
    for f in frames:
        p=dest/f"frame-{f['number']:06d}.jpg";im=cv2.imdecode(np.fromfile(p,dtype=np.uint8),cv2.IMREAD_COLOR)
        if im is None:raise ValueError('Unreadable frame '+str(p))
        gray=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY);h,w=gray.shape
        f.update(file=str(p),width=w,height=h,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),mean_luma=float(gray.mean()),laplacian_variance=float(cv2.Laplacian(gray,cv2.CV_64F).var()),dark_pixel_ratio=float(np.mean(gray<16)),bright_pixel_ratio=float(np.mean(gray>240)),review_status='automatic_unreviewed')
    duration=row['duration'];cuts,shots=build_timeline(frames,duration,threshold)
    transcript=json.loads(Path(row['transcript']).read_text(encoding='utf-8')) if row.get('transcript') else {'segments':[]}
    speech=transcript['segments'];speech_seconds=union_duration([(s['start'],s['end']) for s in speech],duration)
    for f in frames:f['speech_context']=[{'start':s['start'],'end':s['end'],'text':s['text']} for s in speech if s['start']-.15<=f['time']<=s['end']+.15]
    for shot in shots:shot['transcript_segments']=[s for s in speech if s['start']<shot['end'] and s['end']>shot['start']]
    v=next(s for s in row['metadata']['streams'] if s['codec_type']=='video')
    result={'version':VERSION,'source':str(path),'source_sha256':row['sha256'],'source_fingerprint':{'size':st.st_size,'mtime_ns':st.st_mtime_ns},'threshold':threshold,'max_frame_gap':gap,'duration':duration,'video_parameters':{k:v.get(k) for k in ['codec_name','width','height','r_frame_rate','avg_frame_rate','pix_fmt','color_space','color_transfer','bit_rate','side_data_list']},'frames':frames,'candidate_cuts':cuts,'candidate_shots':shots,'production_statistics':{'candidate_shot_count':len(shots),'candidate_cut_rate_per_minute':len(cuts)/duration*60,'median_candidate_shot_seconds':statistics.median(s['duration'] for s in shots),'asr_speech_seconds':speech_seconds,'asr_speech_coverage':speech_seconds/duration,'asr_characters_per_video_second':sum(len(re.sub(r'\W','',s['text'])) for s in speech)/duration},'audio':row.get('audio'),'loudness':row.get('loudness'),'limits':['Cut points are image-change candidates; flashes, titles and fast motion can trigger them; fades and subtle cuts may be missed.','Frame-gap sampling can miss brief typography; exact fonts, transition presets, layers and source edits cannot be recovered from rendered video.','Laplacian variance and luma are scale-dependent measurements, not quality scores.'],'unknown_project_parameters':{'font_file':None,'transition_preset':None,'original_layer_stack':None,'effect_parameters':None}}
    result['decoder']=decoder;result['decode_stderr']=proc.stderr.decode('utf-8',errors='replace')
    result['storyboard']=str(dest/'storyboard.jpg');result['storyboard_frames']=contact(frames,Path(result['storyboard']),Path(row['file']).stem)
    after=path.stat()
    if (after.st_size,after.st_mtime_ns)!=(st.st_size,st.st_mtime_ns):raise ValueError('Source changed during inspection')
    save(result_path,result);return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--out');ap.add_argument('--limit',type=int);ap.add_argument('--gap',type=float,default=5);ap.add_argument('--workers',type=int,default=2);ap.add_argument('--hwaccel',choices=['cuda']);args=ap.parse_args()
    dataset=Path(args.dataset).resolve();out=Path(args.out).resolve() if args.out else dataset/'production-visual';out.mkdir(parents=True,exist_ok=True)
    rows=[r for r in json.loads((dataset/'manifest.json').read_text(encoding='utf-8')) if r['kind']=='video' and not r.get('duplicate_of')]
    if args.limit:rows=rows[:args.limit]
    threshold=json.loads((ROOT/'config/perception.json').read_text(encoding='utf-8'))['scene_cut']
    collected=[];errors=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(collect,r,out,threshold,args.gap,args.hwaccel):r for r in rows}
        for f in concurrent.futures.as_completed(futures):
            r=futures[f]
            try:
                result=f.result();collected.append({'source':r['file'],'sha256':r['sha256'],'production':str(out/'videos'/r['sha256']/'production.json'),'frame_count':len(result['frames']),'candidate_cut_count':len(result['candidate_cuts']),'storyboard':result['storyboard']})
                print(f'{len(collected)}/{len(rows)} frames={len(result["frames"])} cuts={len(result["candidate_cuts"])} {r["relative"]}',flush=True)
            except Exception as e:errors.append({'source':r['file'],'error':repr(e)});print('ERROR '+repr(e),flush=True)
            save(out/'index.json',sorted(collected,key=lambda x:x['source']));save(out/'errors.json',errors)
    save(out/'collection-summary.json',{'videos':len(collected),'expected_videos':len(rows),'frames':sum(r['frame_count'] for r in collected),'candidate_cuts':sum(r['candidate_cut_count'] for r in collected),'errors':errors,'threshold':threshold,'gap':args.gap,'source_files_modified':False})
    if errors:raise SystemExit(1)

if __name__=='__main__':main()
