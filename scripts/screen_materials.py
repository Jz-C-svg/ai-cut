"""Screen indexed source assets with auditable text and FFmpeg signals."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'lib'))
from aicut.material_screening import screen_transcript,scan_media,classify_visual,compare_takes

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--folder',required=True);ap.add_argument('--manifest',default=str(ROOT/'work/manifest.json'));ap.add_argument('--out',required=True);ap.add_argument('--text-only',action='store_true')
    args=ap.parse_args();folder=Path(args.folder).resolve()
    manifest=json.loads(Path(args.manifest).read_text(encoding='utf-8'));reports=[];takes=[]
    for item in manifest['items']:
        if Path(item['source']).resolve().parent!=folder:continue
        file=Path(item['video']);file=file if file.is_absolute() else ROOT/file
        record={'source':item['source'],'normalized':str(file),'status':'failed'}
        try:
            data=json.loads((ROOT/'02-transcript'/f"{item['name']}.json").read_text(encoding='utf-8'))
            import subprocess
            probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(file)]))
            duration=float(probe['format']['duration']);segments=data['segments']
            takes.append({'source':item['source'],'segments':segments})
            text=screen_transcript(segments,duration)
            timestamp_warnings=[{'segment':i+1,'original_end':s['end'],'media_end':duration,
                                 'action':'bounded candidate end only; source transcript retained'}
                                for i,s in enumerate(segments) if s['end']>duration]
            signals=[] if args.text_only else classify_visual(scan_media(file,duration,any(s['codec_type']=='audio' for s in probe['streams'])),segments)
            st=Path(item['source']).stat()
            record.update(status='complete',duration=duration,timestamp_warnings=timestamp_warnings,source_fingerprint={'size':st.st_size,'mtime_ns':st.st_mtime_ns},
                          transcript_path=str(ROOT/'02-transcript'/f"{item['name']}.json"),text_findings=text,media_findings=signals,
                          automatic_removal_seconds=sum(r['end']-r['start'] for r in text if r['action']=='auto_remove'))
        except Exception as exc:record['error']=str(exc)
        reports.append(record)
        target=Path(args.out);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps({'folder':str(folder),'sampling_fps':12,'assets':reports,'take_overlap':compare_takes(takes),'limitations':'candidate detection is not quality or semantic certainty; source media not modified'},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'source':Path(item['source']).name,'status':record['status'],'text_candidates':len(record.get('text_findings',[])),'media_candidates':len(record.get('media_findings',[]))},ensure_ascii=False),flush=True)
    if not reports:raise ValueError('folder has no manifest assets')
    return int(any(r['status']!='complete' for r in reports))

if __name__=='__main__':raise SystemExit(main())
