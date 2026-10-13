"""Search collected filenames and ASR segments, with original time references."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'lib'))
from aicut.media_catalog import terms

def search_dataset(folder,query,limit=10,kind='video'):
    tokens=terms(query);results=[]
    for r in json.loads((Path(folder)/'manifest.json').read_text(encoding='utf-8')):
        if r.get('duplicate_of') or r.get('status')!='ok' or (kind!='all' and r.get('kind')!=kind):continue
        segments=[]
        if r.get('transcript'):
            segments=json.loads(Path(r['transcript']).read_text(encoding='utf-8'))['segments']
        matched=False
        for segment in segments:
            text=segment['text'];overlap=len(tokens&terms(text));score=overlap*3+len(tokens&terms(r['relative']))
            if overlap:
                matched=True
                results.append({'file':r['file'],'sha256':r['sha256'],'score':score,'text':text,'start':segment.get('start'),'end':segment.get('end'),'basis':'automatic_asr_unreviewed','issues':r['issues']})
        if not matched:
            score=len(tokens&terms(r['relative']))
            if score:results.append({'file':r['file'],'sha256':r['sha256'],'score':score,'text':r['relative'],'start':None,'end':None,'basis':'filename_only','issues':r['issues']})
    return sorted(results,key=lambda x:-x['score'])[:limit]

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True);p.add_argument('--query',required=True);p.add_argument('--limit',type=int,default=10);p.add_argument('--kind',choices=['video','audio','image','all'],default='video');a=p.parse_args()
    print(json.dumps(search_dataset(a.dataset,a.query,a.limit,a.kind),ensure_ascii=False,indent=2))
