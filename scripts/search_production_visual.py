"""Retrieve collected frames and production measurements with exact source times."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from aicut.media_catalog import terms

def search(folder,query,limit=10,large_text=False):
    tokens=terms(query);results=[]
    with (Path(folder)/'frame-index.jsonl').open(encoding='utf-8') as f:
        for line in f:
            r=json.loads(line)
            if large_text and not r['large_text_candidate']:continue
            matches=tokens&terms(r['screen_text']+' '+r['speech_text'])
            if query and not matches:continue
            results.append({**r,'score':len(matches),'matched_terms':sorted(matches)})
    return sorted(results,key=lambda r:(-r['score'],r['source'],r['time']))[:limit]

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--collection',required=True);p.add_argument('--query',default='');p.add_argument('--limit',type=int,default=10);p.add_argument('--large-text',action='store_true');a=p.parse_args()
    print(json.dumps(search(a.collection,a.query,a.limit,a.large_text),ensure_ascii=False,indent=2))
