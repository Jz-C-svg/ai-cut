"""Bounded-memory preassembly for long/multi-take exports; preserves original timeline."""
import hashlib,json,subprocess
from pathlib import Path

VERSION='preassembly-v2-render-pipeline'
def assemble(sheet,root,build_command,resolve):
    cache=root/'work/render-cache';cache.mkdir(parents=True,exist_ok=True)
    hashes={};parts=[]
    for index,clip in enumerate(sheet['clips'],1):
        source=Path(resolve(clip['file']))
        if str(source) not in hashes:
            with source.open('rb') as f:hashes[str(source)]=hashlib.file_digest(f,'sha256').hexdigest()
        key=hashlib.sha256(json.dumps([VERSION,hashes[str(source)],clip,sheet.get('width',1080),sheet.get('height',1920),sheet.get('fps',30)],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
        path=cache/(key+'.mkv');marker=cache/(key+'.json')
        duration=(clip['end']-clip['start'])/float(clip.get('speed',1))
        if not path.exists() or not marker.exists():
            temp=cache/(key+'.partial.mkv')
            shifted={**clip,'start':0,'end':clip['end']-clip['start']}
            one={'clips':[shifted],'width':sheet.get('width',1080),'height':sheet.get('height',1920),'fps':sheet.get('fps',30),
                 'render_pipeline':sheet.get('render_pipeline',{})}
            cmd=build_command(one,temp,False)
            pos=cmd.index('-i');cmd[pos:pos]=['-ss',f'{clip["start"]:.6f}']
            cmd[cmd.index('-c:a')+1]='flac';cmd[cmd.index('-crf')+1]='16'
            # Matroska intermediate, lossless audio. Final AAC is encoded once.
            pos=cmd.index('-movflags');del cmd[pos:pos+2]
            pos=cmd.index('-b:a');del cmd[pos:pos+2]
            proc=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace')
            if proc.returncode:raise RuntimeError(f'preassembly clip {index}: {proc.returncode}: {proc.stderr[-1000:]}')
            temp.replace(path);marker.write_text(json.dumps({'version':VERSION,'source_sha256':hashes[str(source)],'clip':clip,'duration':duration},ensure_ascii=False,indent=2),encoding='utf-8')
        parts.append((path,duration));print(f'[分段渲染] {index}/{len(sheet["clips"])} {duration:.2f}s',flush=True)
    identity=hashlib.sha256(json.dumps([(p.name,d) for p,d in parts]).encode()).hexdigest()[:24]
    listing=cache/(identity+'.ffconcat')
    listing.write_text('ffconcat version 1.0\n'+''.join(f"file '{p.name}'\nduration {d:.6f}\n" for p,d in parts),encoding='utf-8')
    total=sum(d for p,d in parts)
    return {**sheet,'clips':[{'file':str(listing),'start':0,'end':total,'speed':1}]}, {'manifest':str(listing),'parts':len(parts),'source_hashes':hashes,'intermediate_video_crf':16,'intermediate_audio':'FLAC'}
