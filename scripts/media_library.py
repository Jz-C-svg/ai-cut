"""Index or query local media. Discovery does not invent visual descriptions."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'lib'))
from aicut.media_catalog import index_library,search
def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--index',required=True)
    p.add_argument('--annotations');p.add_argument('--query');p.add_argument('--limit',type=int,default=10)
    args=p.parse_args();out=Path(args.index)
    old=json.loads(out.read_text(encoding='utf-8')) if out.exists() else {}
    ann=json.loads(Path(args.annotations).read_text(encoding='utf-8')) if args.annotations else {}
    data=index_library(args.root,old,ann);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'assets':len(data['assets']),'matches':search(data,args.query,limit=args.limit) if args.query else []},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
