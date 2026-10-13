"""Render a separate filtered source and mapped transcript; original is retained."""
import argparse,json,sys,subprocess,os,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'lib'))
from aicut.material_screening import screen_transcript
import cleanup

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--video',required=True);ap.add_argument('--transcript',required=True);ap.add_argument('--out',required=True)
    args=ap.parse_args();source=Path(args.video);out=Path(args.out)
    if source.resolve()==out.resolve():raise ValueError('output must not overwrite original')
    data=json.loads(Path(args.transcript).read_text(encoding='utf-8'))
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(source)]))
    total=float(probe['format']['duration']);audio=any(s['codec_type']=='audio' for s in probe['streams'])
    findings=screen_transcript(data['segments'],total,data.get('protected_ranges',[]))
    cuts=cleanup.merge_ranges([[r['start'],r['end']] for r in findings if r['action']=='auto_remove'])
    keeps=cleanup.keep_ranges(cuts,total,cleanup.load_rules())
    if not keeps:raise ValueError('all source excluded; do not render empty video')
    filters=[];labels=[]
    for i,(a,b) in enumerate(keeps):
        filters.append(f'[0:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}]');labels.append(f'[v{i}]')
        if audio:filters.append(f'[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{i}]');labels.append(f'[a{i}]')
    filters.append(''.join(labels)+f'concat=n={len(keeps)}:v=1:a={int(audio)}[v]'+('[a]' if audio else ''))
    out.parent.mkdir(parents=True,exist_ok=True);staged=out.with_name(out.stem+'.'+uuid.uuid4().hex+'.partial.mp4')
    cmd=['ffmpeg','-y','-v','error','-filter_complex_threads','2','-i',str(source),'-filter_complex',';'.join(filters),'-map','[v]']
    if audio:cmd+=['-map','[a]','-c:a','aac','-ar','48000','-b:a','192k']
    cmd+=['-c:v','libx264','-threads','4','-preset','veryfast','-crf','20','-movflags','+faststart',str(staged)]
    subprocess.run(cmd,check=True)
    actual=float(json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(staged)]))['format']['duration'])
    expected=sum(b-a for a,b in keeps)
    if abs(actual-expected)>.15:raise ValueError('filtered render duration mismatch')
    kept=cleanup.drop_hidden(data['segments'],cuts)
    for s in kept:
        s['start']=round(cleanup.map_time(s['start'],cuts),3);s['end']=round(cleanup.map_time(min(s['end'],total),cuts),3)
    result={'segments':kept,'duration':actual,'source':str(source),'source_transcript':args.transcript,'cuts':cuts,'keeps':keeps,'findings':findings,'limitations':'text-authorized cuts only; visual anomalies require review'}
    os.replace(staged,out)
    out.with_suffix('.filtered.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'out':str(out),'duration':actual,'removed_seconds':round(total-actual,3),'cuts':cuts},ensure_ascii=False))

if __name__=='__main__':main()
